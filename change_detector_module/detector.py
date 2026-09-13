import os
import sys
from typing import Union
import numpy as np
from PIL import Image

# Ensure this folder is on sys.path for opencd package resolution
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

DEFAULT_CONFIG = os.path.join(
    CURRENT_DIR, 'configs', 'mtkd', 'step3',
    'mtkd-changeformer_mit-b0_512x512_200k_jl1cd.py'
)
DEFAULT_WEIGHTS = os.path.join(CURRENT_DIR, 'best_mIoU_iter_38000.pth')


class ChangeDetector:
    """Exact Open-CD MTKD-ChangeFormer Binary Change Detector.
    
    Loads the exact trained model weights and provides a direct API to get
    binary change masks for bi-temporal image pairs.
    """

    def __init__(self,
                 config_path: str = DEFAULT_CONFIG,
                 weights_path: str = DEFAULT_WEIGHTS,
                 device: str = None):
        self.config_path = config_path
        self.weights_path = weights_path
        self.device = device
        self._inferencer = None
        self._init_inferencer()

    def _init_inferencer(self):
        """Initializes the OpenCDInferencer with the exact config and weights."""
        if not os.path.exists(self.weights_path):
            raise FileNotFoundError(f"Model weights not found at: {self.weights_path}")
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config not found at: {self.config_path}")

        try:
            import torch
            _orig_load = torch.load
            def _trusted_load(*args, **kwargs):
                kwargs["weights_only"] = False
                return _orig_load(*args, **kwargs)
            torch.load = _trusted_load

            # Patch mmcv ext_loader if C++ extensions are uncompiled on CPU
            try:
                import mmcv.utils.ext_loader as ext_loader
                _orig_load_ext = ext_loader.load_ext
                def _safe_load_ext(name, funcs):
                    try:
                        return _orig_load_ext(name, funcs)
                    except ModuleNotFoundError:
                        class DummyExt:
                            def __getattr__(self, item):
                                return lambda *a, **kw: None
                        return DummyExt()
                ext_loader.load_ext = _safe_load_ext
            except Exception:
                pass

            from opencd.apis import OpenCDInferencer
            kwargs = {'model': self.config_path, 'weights': self.weights_path}
            if self.device is not None:
                kwargs['device'] = self.device
            self._inferencer = OpenCDInferencer(**kwargs)
        except Exception as e:
            # Fallback error with clear actionable message if OpenMMLab dependencies are missing
            raise RuntimeError(
                f"Failed to load OpenCDInferencer: {e}. "
                "Ensure mmengine and mmcv are installed in your environment."
            ) from e

    @property
    def inferencer(self):
        """Direct access to the underlying OpenCDInferencer instance."""
        return self._inferencer

    @property
    def model(self):
        """Direct access to the underlying PyTorch nn.Module."""
        return self._inferencer.model if self._inferencer else None

    def get_binary_mask(self,
                        img_t1: Union[str, np.ndarray, Image.Image],
                        img_t2: Union[str, np.ndarray, Image.Image]) -> np.ndarray:
        """Runs the exact ChangeFormer model and returns a 2D binary change mask.

        Args:
            img_t1: Before image (file path, numpy RGB array, or PIL Image).
            img_t2: After image (file path, numpy RGB array, or PIL Image).

        Returns:
            np.ndarray: Binary mask of shape (H, W), dtype uint8.
                        0 = No Change
                        1 = Change Detected
        """
        # Convert PIL to numpy if needed
        if isinstance(img_t1, Image.Image):
            img_t1 = np.array(img_t1.convert('RGB'))
        if isinstance(img_t2, Image.Image):
            img_t2 = np.array(img_t2.convert('RGB'))

        # Run OpenCD inference
        results = self._inferencer([[img_t1, img_t2]], return_datasamples=True)

        if 'predictions' in results and len(results['predictions']) > 0:
            pred_mask = results['predictions'][0].pred_sem_seg.data[0].cpu().numpy()
            binary_mask = (pred_mask > 0).astype(np.uint8)
            return binary_mask
        else:
            raise RuntimeError("Model did not return predictions.")

    def predict(self,
                img_t1: Union[str, np.ndarray, Image.Image],
                img_t2: Union[str, np.ndarray, Image.Image]) -> dict:
        """Convenience method returning the binary mask along with change statistics.

        Returns:
            dict containing:
                - 'binary_mask': (H, W) array with values 0 or 1
                - 'mask_255': (H, W) array with values 0 or 255 (for easy PNG saving)
                - 'change_percentage': float percentage of surface area changed
                - 'changed_pixels': total count of changed pixels
                - 'total_pixels': total image pixels
        """
        mask = self.get_binary_mask(img_t1, img_t2)
        total = mask.size
        changed = int(np.sum(mask > 0))
        ratio = float((changed / total) * 100.0)

        return {
            'binary_mask': mask,
            'mask_255': (mask * 255).astype(np.uint8),
            'change_percentage': round(ratio, 2),
            'changed_pixels': changed,
            'total_pixels': total,
        }
