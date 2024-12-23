from typing import NamedTuple

import torch
from jaxtyping import Float
from torch import Tensor, nn

from src.models.hooked_model import WhisperActivationCache, activations_from_audio
from src.models.config import L1AutoEncoderConfig
from src.utils.models import get_n_dict_components

# modified from
# https://github.com/er537/whisper_interpretability/tree/master/whisper_interpretability/sparse_coding/train/autoencoder.py


class L1EncoderOutput(NamedTuple):
    latent: Tensor


class L1ForwardOutput(NamedTuple):
    sae_out: Tensor

    encoded: L1EncoderOutput

    l1_loss: Tensor

    reconstruction_loss: Tensor


def mse_loss(input, target, ignored_index, reduction):
    # mse_loss with ignored_index
    mask = target == ignored_index
    out = (input[~mask] - target[~mask]) ** 2
    if reduction == "mean":
        return out.mean()
    elif reduction == "None":
        return out


class L1AutoEncoder(nn.Module):
    def __init__(self, activation_size: int, cfg: L1AutoEncoderConfig):
        """
        Autoencoder model for audio features

        :param cfg: model configuration
        """
        super(L1AutoEncoder, self).__init__()
        self.cfg = cfg
        self.tied = True  # tie encoder and decoder weights
        self.activation_size = activation_size
        self.n_dict_components = get_n_dict_components(
            activation_size, cfg.expansion_factor, cfg.n_dict_components
        )
        self.recon_alpha = cfg.recon_alpha

        # Only defining the decoder layer, encoder will share its weights
        self.decoder = nn.Linear(
            self.n_dict_components, self.activation_size, bias=False
        )
        # Create a bias layer
        self.encoder_bias = nn.Parameter(torch.zeros(self.n_dict_components))

        # Initialize the decoder weights orthogonally
        nn.init.orthogonal_(self.decoder.weight)

        # Encoder is a Sequential with the ReLU activation
        # No need to define a Linear layer for the encoder as its weights are tied with the decoder
        self.encoder = nn.Sequential(nn.ReLU())

    def encode(self, x: Float[Tensor, "bsz seq_len d_model"]):  # noqa: F821
        # Apply unit norm constraint to the decoder weights
        self.decoder.weight.data = nn.functional.normalize(
            self.decoder.weight.data, dim=0
        )
        c = self.encoder(x @ self.decoder.weight + self.encoder_bias)
        return L1EncoderOutput(latent=c)

    def decode(self, c: Float[Tensor, "bsz seq_len n_dict_components"]):  # noqa: F821
        return self.decoder(c)

    def forward(
        self, x: Float[Tensor, "bsz seq_len d_model"], return_mse: bool = False
    ):  # noqa: F821
        c = self.encode(x).latent
        x_hat = self.decoder(c)
        loss_l1 = torch.norm(c, 1, dim=2).mean()
        loss_recon = self.recon_alpha * mse_loss(x_hat, x, -1, "mean")
        forward_output = L1ForwardOutput(
            sae_out=x_hat,
            encoded=L1EncoderOutput(c),
            l1_loss=loss_l1,
            reconstruction_loss=loss_recon,
        )
        if return_mse:
            return forward_output, ((x_hat - x) ** 2).mean()
        return forward_output
