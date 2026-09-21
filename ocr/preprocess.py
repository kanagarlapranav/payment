def preprocess_image_for_ocr(image_path: str) -> str:
    try:
        import cv2
        import numpy as np
    except Exception:
        return image_path

    try:
        img = cv2.imread(image_path)
        if img is None:
            return image_path
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape
        if height < 1000 or width < 1000:
            gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        gray = cv2.bilateralFilter(gray, 5, 50, 50)
        import os
        root, ext = os.path.splitext(image_path)
        ext = ext if ext else '.jpg'
        processed_path = f"{root}_processed{ext}"
        cv2.imwrite(processed_path, gray)
        return processed_path
    except Exception:
        return image_path

