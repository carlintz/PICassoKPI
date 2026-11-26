from threading import local

import cv2
import numpy as np


class Vision:
    def __init__(self, models, references=(), changed_brands=()):
        self.detector = models / "face_detection_yunet_2023mar.onnx"
        self.recognizer = models / "face_recognition_sface_2021dec.onnx"
        self.local = local()
        self.changed_brands = changed_brands
        grouped = {}
        for reference in references:
            grouped.setdefault(reference["brand"], []).append(reference["embedding"])
        self.references = {brand: np.asarray(vectors, dtype=np.float32)
                           for brand, vectors in grouped.items()}

    @property
    def can_compare(self):
        return self.detector.is_file() and self.recognizer.is_file()

    def reference_embedding(self, picture):
        image = cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)
        faces = self.detect(image)
        if len(faces) != 1:
            raise ValueError("얼굴 한 개가 선명하게 보이는 이미지를 선택하세요.")
        return self.feature(image, faces[0]).tolist()

    def detect(self, image):
        if image is None:
            return []
        if not hasattr(self.local, "detector"):
            self.local.detector = cv2.FaceDetectorYN.create(str(self.detector), "", (320, 320), .68)
        detector = self.local.detector
        detector.setInputSize((image.shape[1], image.shape[0]))
        _, faces = detector.detect(image)
        return [] if faces is None else faces

    def feature(self, image, face):
        if not hasattr(self.local, "recognizer"):
            self.local.recognizer = cv2.FaceRecognizerSF.create(str(self.recognizer), "")
        recognizer = self.local.recognizer
        vector = recognizer.feature(recognizer.alignCrop(image, face)).ravel()
        norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm == 0:
            raise ValueError("얼굴 특징을 추출할 수 없습니다.")
        return vector / norm

    def classify(self, picture, brand):
        if not self.detector.exists():
            return {}
        image = cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)
        faces = self.detect(image)
        if not len(faces):
            return {"thumbnail_type": "product_only", "similarity": "not_applicable"}
        result = {"thumbnail_type": "model"}
        references = self.references.get(brand)
        if references is not None:
            faces = np.stack([self.feature(image, face) for face in faces])
            score = float(np.clip((faces @ references.T).max(), -1, 1))
            result.update(score=round(score, 3),
                          similarity="similar" if score >= .45 else "unclear" if score >= .32 else "other")
        return result
