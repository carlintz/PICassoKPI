from threading import local

import cv2
import numpy as np


class Vision:
    def __init__(self, models, reference, reference_changed=False):
        self.models = models
        self.detector = models / "face_detection_yunet_2023mar.onnx"
        self.recognizer = models / "face_recognition_sface_2021dec.onnx"
        self.local = local()
        self.reference_changed = reference_changed
        self.vector = None
        if reference.exists() and self.detector.exists() and self.recognizer.exists():
            image = cv2.imread(str(reference))
            faces = self.detect(image)
            if len(faces) == 1:
                self.vector = self.feature(image, faces[0])

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
        return vector / np.linalg.norm(vector)

    def classify(self, picture, compare):
        if not self.detector.exists():
            return {}
        image = cv2.cvtColor(np.asarray(picture), cv2.COLOR_RGB2BGR)
        faces = self.detect(image)
        if not len(faces):
            return {"thumbnail_type": "product_only", "similarity": "not_applicable"}
        result = {"thumbnail_type": "model"}
        if compare and self.vector is not None:
            score = max(float(self.feature(image, face) @ self.vector) for face in faces)
            result.update(score=round(score, 3),
                          similarity="similar" if score >= .45 else "unclear" if score >= .32 else "other")
        return result
