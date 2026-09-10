# 🛰️ Change Detection Module (MTKD-ChangeFormer)

A clean, self-contained module containing the exact **MTKD-ChangeFormer (MiT-B0)** model and pretrained weights from Open-CD, stripped of all unused models, datasets, and training pipelines.

---

## 📁 Module Structure

```text
change_detector_module/
├── best_mIoU_iter_38000.pth      # 🔒 EXACT original model weights
├── configs/                      # ONLY the required model configs
│   ├── mtkd/step3/mtkd-changeformer_mit-b0_512x512_200k_jl1cd.py
│   ├── _base_/models/mtkd/mtkd-changeformer_mit-b0.py
│   ├── _base_/default_runtime.py
│   └── common/standard_512x512_200k_jl1cd.py
├── opencd/                       # PRUNED opencd (only required modules)
├── detector.py                   # Main API wrapper
├── example.py                    # Usage test script
└── requirements.txt              # Dependencies
```

---

## 🚀 How to Use in Another Project

### 1. Copy the folder
Copy the `change_detector_module/` folder into your other project directory:
```text
your_project/
├── your_script.py
└── change_detector_module/
```

### 2. Import and Run
```python
from change_detector_module.detector import ChangeDetector

# Initialize detector (automatically locates its own weights and configs)
detector = ChangeDetector()

# Pass image paths, PIL Images, or NumPy arrays:
# Returns a 2D numpy array (H, W) where 0 = Unchanged, 1 = Changed
binary_mask = detector.get_binary_mask("before.png", "after.png")

print(f"Mask shape: {binary_mask.shape}")
print(f"Changed pixels: {(binary_mask == 1).sum()}")
```

### 3. If you want Change Statistics or Mask for PNG saving:
```python
results = detector.predict("before.png", "after.png")

print(f"Change: {results['change_percentage']}%")
# results['mask_255'] has values 0 and 255 for direct PNG saving
from PIL import Image
Image.fromarray(results['mask_255']).save("change_mask.png")
```

### 4. Direct Access to the PyTorch Model:
```python
model = detector.model  # Returns the underlying PyTorch nn.Module
```
