from PIL import Image
from torchvision.transforms.functional import to_tensor
from cgd.util import resize_image, download
import clip
from functools import lru_cache
import torch as th
import torch.nn.functional as tf
import torchvision.transforms as tvt
from data.imagenet1000_clsidx_to_labels import IMAGENET_CLASSES


CLIP_MODEL_NAMES = ("ViT-B/16", "ViT-B/32", "RN50", "RN101", "RN50x4", "RN50x16")
CLIP_NORMALIZE = tvt.Normalize(mean=[0.48145466, 0.4578275, 0.40821073], std=[0.26862954, 0.26130258, 0.27577711])

@lru_cache(maxsize=1)
def load_clip(model_name='ViT-B/32', device="cpu"):
    print(f"Loading clip model\t{model_name}\ton device\t{device}.")
    if device == "cpu":
        clip_model = clip.load(model_name, jit=False)[0].eval().to(device=device).float()
        clip_size = clip_model.visual.input_resolution
        return clip_model, clip_size
    elif "cuda" in device:
        clip_model = clip.load(model_name, jit=False)[0].eval().requires_grad_(False).to(device)
        clip_size = clip_model.visual.input_resolution
        return clip_model, clip_size
    else:
        raise ValueError("Invalid or unspecified device: {}".format(device))

class MakeCutouts(th.nn.Module):
    def __init__(self, cut_size: int, num_cutouts: int, cutout_size_power: float = 1.0, use_augs: bool = True):
        super().__init__()
        self.cut_size = cut_size
        self.cutn = num_cutouts
        self.cut_pow = cutout_size_power
        custom_augs = []
        if use_augs:
            custom_augs = [
                tvt.RandomHorizontalFlip(p=0.5),
                tvt.Lambda(lambda x: x + th.randn_like(x) * 0.01),
                tvt.RandomAffine(degrees=15, translate=(0.1, 0.1)),
                tvt.Lambda(lambda x: x + th.randn_like(x) * 0.01),
                tvt.RandomPerspective(distortion_scale=0.4, p=0.7),
                tvt.Lambda(lambda x: x + th.randn_like(x) * 0.01),
                tvt.RandomGrayscale(p=0.15),
                tvt.Lambda(lambda x: x + th.randn_like(x) * 0.01),
                tvt.ColorJitter(brightness=0.1, contrast=0.1, saturation=0.1, hue=0.1),
                tvt.Lambda(lambda x: x + th.randn_like(x) * 0.01),
            ] # TODO: test color jitter specifically
        self.augs = tvt.Compose(custom_augs)

    def forward(self, input: th.Tensor):
        side_x, side_y = input.shape[2:4]
        max_size = min(side_y, side_x)
        min_size = min(side_y, side_x, self.cut_size)
        cutouts = []
        for _ in range(self.cutn):
            size = int(th.rand([])**self.cut_pow * (max_size - min_size) + min_size)
            offsetx = th.randint(0, side_x - size + 1, ())
            offsety = th.randint(0, side_y - size + 1, ())
            cutout = input[:, :, offsety:offsety + size, offsetx:offsetx + size]
            cutouts.append(tf.adaptive_avg_pool2d(cutout, self.cut_size))
        return th.cat(cutouts)


def imagenet_top_n(text_encodes, device: str = 'cuda', n: int = len(IMAGENET_CLASSES), clip_model_name: str = "ViT-B/32"):
    """
    Returns the top n classes for a given clip model.
    """
    clip_model, _ = load_clip(model_name=clip_model_name, device=device)
    with th.no_grad():
        engineered_pronmpts = [f"an image of a {img_cls}" for img_cls in IMAGENET_CLASSES]
        imagenet_lbl_tokens = clip.tokenize(engineered_pronmpts).to(device)
        imagenet_features = clip_model.encode_text(imagenet_lbl_tokens).float()
        imagenet_features /= imagenet_features.norm(dim=-1, keepdim=True)
        prompt_features = text_encodes / text_encodes.norm(dim=-1, keepdim=True)
        text_probs = (100.0 * prompt_features @ imagenet_features.T).softmax(dim=-1)
        return text_probs.topk(n, dim=-1, sorted=True).indices[0].to(device)