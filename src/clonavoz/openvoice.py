"""Conversor de timbre de OpenVoice V2: cambia el timbre de un audio ya
sintetizado (la voz rápida de Piper) por el de tu voz, a partir de tu muestra
de `clonavoz enroll`. Es lo que permite clonar tu voz en cualquier idioma que
tenga una voz de Piper, unas 10 veces más rápido que XTTS-v2 en CPU y con
muy poca memoria (el modelo pesa ~130 MB).

Es una versión recortada, solo para inferencia, del modelo de OpenVoice
(https://github.com/myshell-ai/OpenVoice: openvoice/models.py, modules.py,
commons.py y mel_processing.py), con los mismos nombres de parámetros para
cargar el checkpoint oficial. La normalización de pesos (weight_norm) se
"pliega" al cargar, así que no depende de esa API de PyTorch (deprecada).

Copyright (c) 2024 MyShell.ai. Licencia MIT:

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import math

import numpy as np
import torch
from scipy.signal import resample_poly
from torch import nn
from torch.nn import functional as F

SAMPLE_RATE = 22050
_REPO = "myshell-ai/OpenVoiceV2"
_REVISION = "f36e7edfe1684461a8343844af60babc2efbb727"  # fija: siempre el mismo checkpoint
_N_FFT, _HOP = 1024, 256
_SPEC_CHANNELS = _N_FFT // 2 + 1
_HIDDEN = 192  # canales internos (inter_channels = hidden_channels)
_GIN = 256  # tamaño de la "huella" de timbre
_LRELU_SLOPE = 0.1


class _WN(nn.Module):
    """Pila tipo WaveNet condicionada por la huella de timbre `g`."""

    def __init__(self, n_layers: int, kernel_size: int = 5) -> None:
        super().__init__()
        self.n_layers = n_layers
        self.cond_layer = nn.Conv1d(_GIN, 2 * _HIDDEN * n_layers, 1)
        self.in_layers = nn.ModuleList(
            nn.Conv1d(_HIDDEN, 2 * _HIDDEN, kernel_size, padding=kernel_size // 2) for _ in range(n_layers)
        )
        self.res_skip_layers = nn.ModuleList(
            nn.Conv1d(_HIDDEN, 2 * _HIDDEN if i < n_layers - 1 else _HIDDEN, 1) for i in range(n_layers)
        )

    def forward(self, x, mask, g):
        output = torch.zeros_like(x)
        g = self.cond_layer(g)
        for i in range(self.n_layers):
            x_in = self.in_layers[i](x) + g[:, 2 * _HIDDEN * i : 2 * _HIDDEN * (i + 1)]
            acts = torch.tanh(x_in[:, :_HIDDEN]) * torch.sigmoid(x_in[:, _HIDDEN:])
            res_skip = self.res_skip_layers[i](acts)
            if i < self.n_layers - 1:
                x = (x + res_skip[:, :_HIDDEN]) * mask
                output = output + res_skip[:, _HIDDEN:]
            else:
                output = output + res_skip
        return output * mask


class _PosteriorEncoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.pre = nn.Conv1d(_SPEC_CHANNELS, _HIDDEN, 1)
        self.enc = _WN(n_layers=16)
        self.proj = nn.Conv1d(_HIDDEN, 2 * _HIDDEN, 1)

    def forward(self, spec, mask, g, tau):
        x = self.enc(self.pre(spec) * mask, mask, g)
        m, logs = torch.split(self.proj(x) * mask, _HIDDEN, dim=1)
        return (m + torch.randn_like(m) * tau * torch.exp(logs)) * mask


class _CouplingLayer(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.pre = nn.Conv1d(_HIDDEN // 2, _HIDDEN, 1)
        self.enc = _WN(n_layers=4)
        self.post = nn.Conv1d(_HIDDEN, _HIDDEN // 2, 1)

    def forward(self, x, mask, g, reverse):
        x0, x1 = torch.split(x, _HIDDEN // 2, dim=1)
        m = self.post(self.enc(self.pre(x0) * mask, mask, g)) * mask
        x1 = (x1 - m) * mask if reverse else m + x1 * mask
        return torch.cat([x0, x1], 1)


class _Flip(nn.Module):
    def forward(self, x, mask, g, reverse):
        return torch.flip(x, [1])


class _Flow(nn.Module):
    """Saca el timbre de origen (hacia adelante) o pone el de destino (en reversa)."""

    def __init__(self) -> None:
        super().__init__()
        self.flows = nn.ModuleList()
        for _ in range(4):
            self.flows.append(_CouplingLayer())
            self.flows.append(_Flip())

    def forward(self, x, mask, g, reverse=False):
        for flow in reversed(self.flows) if reverse else self.flows:
            x = flow(x, mask, g, reverse)
        return x


class _ResBlock(nn.Module):
    def __init__(self, channels: int, kernel_size: int) -> None:
        super().__init__()
        self.convs1 = nn.ModuleList(
            nn.Conv1d(channels, channels, kernel_size, dilation=d, padding=(kernel_size * d - d) // 2)
            for d in (1, 3, 5)
        )
        self.convs2 = nn.ModuleList(
            nn.Conv1d(channels, channels, kernel_size, padding=kernel_size // 2) for _ in range(3)
        )

    def forward(self, x):
        for c1, c2 in zip(self.convs1, self.convs2, strict=True):
            x = x + c2(F.leaky_relu(c1(F.leaky_relu(x, _LRELU_SLOPE)), _LRELU_SLOPE))
        return x


class _Generator(nn.Module):
    """Vocoder HiFi-GAN: de representación interna a forma de onda."""

    def __init__(self) -> None:
        super().__init__()
        self.conv_pre = nn.Conv1d(_HIDDEN, 512, 7, padding=3)
        self.cond = nn.Conv1d(_GIN, 512, 1)
        self.ups = nn.ModuleList()
        self.resblocks = nn.ModuleList()
        for i, (rate, kernel) in enumerate(zip((8, 8, 2, 2), (16, 16, 4, 4), strict=True)):
            channels = 512 // 2 ** (i + 1)
            self.ups.append(nn.ConvTranspose1d(2 * channels, channels, kernel, rate, padding=(kernel - rate) // 2))
            for resblock_kernel in (3, 7, 11):
                self.resblocks.append(_ResBlock(channels, resblock_kernel))
        self.conv_post = nn.Conv1d(channels, 1, 7, padding=3, bias=False)

    def forward(self, x, g):
        x = self.conv_pre(x) + self.cond(g)
        for i, up in enumerate(self.ups):
            x = up(F.leaky_relu(x, _LRELU_SLOPE))
            x = sum(self.resblocks[3 * i + j](x) for j in range(3)) / 3
        return torch.tanh(self.conv_post(F.leaky_relu(x)))


class _ReferenceEncoder(nn.Module):
    """Espectrograma de una voz -> huella de timbre de 256 números."""

    def __init__(self) -> None:
        super().__init__()
        filters = [1, 32, 32, 64, 64, 128, 128]
        self.convs = nn.ModuleList(
            nn.Conv2d(filters[i], filters[i + 1], kernel_size=3, stride=2, padding=1) for i in range(6)
        )
        freqs = _SPEC_CHANNELS
        for _ in range(6):
            freqs = (freqs - 1) // 2 + 1
        self.gru = nn.GRU(input_size=128 * freqs, hidden_size=128, batch_first=True)
        self.proj = nn.Linear(128, _GIN)
        self.layernorm = nn.LayerNorm(_SPEC_CHANNELS)

    def forward(self, spec):  # spec: [1, frames, frecuencias]
        out = self.layernorm(spec.unsqueeze(1))
        for conv in self.convs:
            out = F.relu(conv(out))
        out = out.transpose(1, 2).flatten(2)
        _, hidden = self.gru(out)
        return self.proj(hidden.squeeze(0))


class _Converter(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.dec = _Generator()
        self.enc_q = _PosteriorEncoder()
        self.flow = _Flow()
        self.ref_enc = _ReferenceEncoder()


def _fold_weight_norm(state: dict) -> dict:
    """weight_g/weight_v (torch.nn.utils.weight_norm, dim=0) -> weight."""
    folded = {}
    for key, value in state.items():
        if key.endswith(".weight_v"):
            continue
        if key.endswith(".weight_g"):
            weight_v = state[key[: -len("_g")] + "_v"]
            norm = weight_v.flatten(1).norm(dim=1).view(-1, *[1] * (weight_v.dim() - 1))
            folded[key[: -len("_g")]] = weight_v * (value / norm)
        else:
            folded[key] = value
    return folded


def _spectrogram(audio: torch.Tensor) -> torch.Tensor:
    pad = (_N_FFT - _HOP) // 2
    audio = F.pad(audio.view(1, 1, -1), (pad, pad), mode="reflect").view(1, -1)
    spec = torch.stft(
        audio, _N_FFT, hop_length=_HOP, win_length=_N_FFT, window=torch.hann_window(_N_FFT),
        center=False, return_complex=True,
    )
    return torch.sqrt(spec.real**2 + spec.imag**2 + 1e-6)


class ToneColorConverter:
    def __init__(self, state_dict: dict | None = None) -> None:
        """Sin `state_dict` los pesos quedan al azar (solo sirve para pruebas):
        para usarlo de verdad, `ToneColorConverter.from_pretrained()`."""
        self._model = _Converter().eval()
        if state_dict is not None:
            self._model.load_state_dict(_fold_weight_norm(state_dict))

    @classmethod
    def from_pretrained(cls) -> "ToneColorConverter":
        """Descarga el checkpoint oficial la primera vez (~130 MB) y después
        lo usa desde el caché local, sin conexión."""
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(_REPO, "converter/checkpoint.pth", revision=_REVISION)
        return cls(torch.load(path, map_location="cpu", weights_only=True)["model"])

    @staticmethod
    def _prepare(audio: np.ndarray, sample_rate: int) -> torch.Tensor:
        audio = np.asarray(audio, dtype=np.float32)
        if sample_rate != SAMPLE_RATE:
            g = math.gcd(sample_rate, SAMPLE_RATE)
            audio = resample_poly(audio, SAMPLE_RATE // g, sample_rate // g).astype(np.float32)
        return torch.from_numpy(audio)

    @torch.inference_mode()
    def embedding(self, audio: np.ndarray, sample_rate: int) -> torch.Tensor:
        """Huella de timbre de una voz (conviene usar 5 segundos o más)."""
        spec = _spectrogram(self._prepare(audio, sample_rate))
        return self._model.ref_enc(spec.transpose(1, 2)).unsqueeze(-1)

    @torch.inference_mode()
    def convert(
        self, audio: np.ndarray, sample_rate: int, source: torch.Tensor, target: torch.Tensor, tau: float = 0.3
    ) -> np.ndarray:
        """Devuelve `audio` con el timbre `target` en vez de `source`, a
        SAMPLE_RATE (22050 Hz)."""
        wav = self._prepare(audio, sample_rate)
        if len(wav) <= _N_FFT:
            return wav.numpy()
        spec = _spectrogram(wav)
        mask = torch.ones(1, 1, spec.size(-1))
        model = self._model
        z = model.enc_q(spec, mask, torch.zeros_like(source), tau)
        z = model.flow(model.flow(z, mask, source), mask, target, reverse=True)
        return model.dec(z * mask, torch.zeros_like(target))[0, 0].numpy()
