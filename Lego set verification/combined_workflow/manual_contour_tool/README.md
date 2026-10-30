# Adjustable LEGO contour tool

Open this folder in Visual Studio Code and run:

```powershell
python app.py
```

Install dependencies once if needed:

```powershell
python -m pip install -r requirements.txt
```

No `.exe` packaging is needed. Choose a photograph, adjust the detection values, and select **Analyze / Refresh**. Click false boxes in the preview to turn them red, then select **Remove selected boxes**. When the preview looks right, select **Save original crops**.

The saved files include their run identifier: `images` contains `image_001.png`, `image_002.png`, etc.; `images01` contains `image01_001.png`, and so on. They are rectangular crops copied directly from the original decoded photograph. Detection smoothing and masking are never applied to those crop pixels. Each run uses the next available folder: `images`, `images01`, `images02`, and so on.

Only Brickognize-ready crop images are placed in `images*`. The numbered overview, mask, and CSV are stored separately in the matching `analysis*` folder.

The preview has three box-editing modes. **Select / delete** marks false detections in red, **Draw new box** adds a missing part by click-dragging its boundary, and **Resize selected box** replaces one selected detection with a newly dragged rectangle. The configured crop padding is applied around both automatic and manual boxes when original pixels are saved.

The **Background** selector supports **Auto**, **Dark**, and **White**. Auto examines the photograph and selects the appropriate mask. White mode combines color saturation with local paper contrast so dark, colored, and light pieces can be found despite uneven illumination. **White-bg saturation** and **White-bg contrast** can be adjusted for difficult photographs.
