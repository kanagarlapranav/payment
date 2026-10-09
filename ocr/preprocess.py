def preprocess_image_for_ocr(image_path: str) -> str:
    try:
        import cv2
        import numpy as np
    except Exception:
        return image_path

    try:
        import os
        # Read image safely with unicode path support on Windows
        try:
            with open(image_path, "rb") as f:
                img_array = np.frombuffer(f.read(), np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception:
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

        root, ext = os.path.splitext(image_path)
        ext = ext if ext else '.jpg'
        processed_path = f"{root}_processed{ext}"

        # Write image safely with unicode path support using cv2.imencode + .tofile
        success, buf = cv2.imencode(ext, gray)
        if success:
            buf.tofile(processed_path)
            return processed_path
        else:
            cv2.imwrite(processed_path, gray)
            return processed_path
    except Exception:
        return image_path
