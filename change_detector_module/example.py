"""Minimal example showing how to use the exact ChangeDetector in any project."""
import numpy as np
from detector import ChangeDetector

def main():
    print("Initializing ChangeDetector...")
    # ChangeDetector automatically uses the local weights and configs
    detector = ChangeDetector()

    # Create synthetic test pair (or load your real images: "t1.png", "t2.png")
    h, w = 512, 512
    img1 = np.zeros((h, w, 3), dtype=np.uint8)
    img2 = img1.copy()
    img2[100:200, 100:200] = 255  # simulated change

    # Get binary mask directly:
    # 0 = Unchanged, 1 = Changed
    binary_mask = detector.get_binary_mask(img1, img2)
    print(f"Binary mask computed! Shape: {binary_mask.shape}, dtype: {binary_mask.dtype}")
    print(f"Changed pixels: {np.sum(binary_mask == 1)}")

if __name__ == '__main__':
    main()
