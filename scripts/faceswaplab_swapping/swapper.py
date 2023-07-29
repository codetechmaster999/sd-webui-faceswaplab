import copy
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Set, Tuple, Optional
import tempfile

import cv2
import insightface
import numpy as np
from insightface.app.common import Face

from PIL import Image
from sklearn.metrics.pairwise import cosine_similarity

from scripts.faceswaplab_swapping import upscaled_inswapper
from scripts.faceswaplab_utils.imgutils import (
    pil_to_cv2,
    check_against_nsfw,
)
from scripts.faceswaplab_utils.faceswaplab_logging import logger, save_img_debug
from scripts import faceswaplab_globals
from modules.shared import opts
from functools import lru_cache
from scripts.faceswaplab_ui.faceswaplab_unit_settings import FaceSwapUnitSettings
from scripts.faceswaplab_postprocessing.postprocessing import enhance_image
from scripts.faceswaplab_postprocessing.postprocessing_options import (
    PostProcessingOptions,
)
from scripts.faceswaplab_utils.models_utils import get_current_model


providers = ["CPUExecutionProvider"]


def cosine_similarity_face(face1: Face, face2: Face) -> float:
    """
    Calculates the cosine similarity between two face embeddings.

    Args:
        face1 (Face): The first face object containing an embedding.
        face2 (Face): The second face object containing an embedding.

    Returns:
        float: The cosine similarity between the face embeddings.

    Note:
        The cosine similarity ranges from 0 to 1, where 1 indicates identical embeddings and 0 indicates completely
        dissimilar embeddings. In this implementation, the similarity is clamped to a minimum value of 0 to ensure a
        non-negative similarity score.
    """
    # Reshape the face embeddings to have a shape of (1, -1)
    vec1 = face1.embedding.reshape(1, -1)
    vec2 = face2.embedding.reshape(1, -1)

    # Calculate the cosine similarity between the reshaped embeddings
    similarity = cosine_similarity(vec1, vec2)

    # Return the maximum of 0 and the calculated similarity as the final similarity score
    return max(0, similarity[0, 0])


def compare_faces(img1: Image.Image, img2: Image.Image) -> float:
    """
    Compares the similarity between two faces extracted from images using cosine similarity.

    Args:
        img1: The first image containing a face.
        img2: The second image containing a face.

    Returns:
        A float value representing the similarity between the two faces (0 to 1).
        Returns -1 if one or both of the images do not contain any faces.
    """

    # Extract faces from the images
    face1 = get_or_default(get_faces(pil_to_cv2(img1)), 0, None)
    face2 = get_or_default(get_faces(pil_to_cv2(img2)), 0, None)

    # Check if both faces are detected
    if face1 is not None and face2 is not None:
        # Calculate the cosine similarity between the faces
        return cosine_similarity_face(face1, face2)

    # Return -1 if one or both of the images do not contain any faces
    return -1


def batch_process(
    src_images: List[Image.Image],
    save_path: Optional[str],
    units: List[FaceSwapUnitSettings],
    postprocess_options: PostProcessingOptions,
) -> Optional[List[Image.Image]]:
    try:
        if save_path:
            os.makedirs(save_path, exist_ok=True)

        units = [u for u in units if u.enable]
        if src_images is not None and len(units) > 0:
            result_images = []
            for src_image in src_images:
                current_images = []
                swapped_images = process_images_units(
                    get_current_model(),
                    images=[(src_image, None)],
                    units=units,
                    upscaled_swapper=opts.data.get(
                        "faceswaplab_upscaled_swapper", False
                    ),
                )
                if len(swapped_images) > 0:
                    current_images += [img for img, _ in swapped_images]

                logger.info("%s images generated", len(current_images))
                for i, img in enumerate(current_images):
                    current_images[i] = enhance_image(img, postprocess_options)

                if save_path:
                    for img in current_images:
                        path = tempfile.NamedTemporaryFile(
                            delete=False, suffix=".png", dir=save_path
                        ).name
                        img.save(path)

                result_images += current_images
            return result_images
    except Exception as e:
        logger.error("Batch Process error : %s", e)
        import traceback

        traceback.print_exc()
    return None


class FaceModelException(Exception):
    """Exception raised when an error is encountered in the face model."""

    def __init__(self, message: str) -> None:
        """
        Args:
            message: A string containing the error description.
        """
        self.message = message
        super().__init__(self.message)


@lru_cache(maxsize=1)
def getAnalysisModel() -> insightface.app.FaceAnalysis:
    """
    Retrieves the analysis model for face analysis.

    Returns:
        insightface.app.FaceAnalysis: The analysis model for face analysis.
    """
    try:
        if not os.path.exists(faceswaplab_globals.ANALYZER_DIR):
            os.makedirs(faceswaplab_globals.ANALYZER_DIR)

        logger.info("Load analysis model, will take some time.")
        # Initialize the analysis model with the specified name and providers
        return insightface.app.FaceAnalysis(
            name="buffalo_l", providers=providers, root=faceswaplab_globals.ANALYZER_DIR
        )
    except Exception as e:
        logger.error(
            "Loading of swapping model failed, please check the requirements (On Windows, download and install Visual Studio. During the install, make sure to include the Python and C++ packages.)"
        )
        raise FaceModelException("Loading of analysis model failed")


@lru_cache(maxsize=1)
def getFaceSwapModel(model_path: str) -> upscaled_inswapper.UpscaledINSwapper:
    """
    Retrieves the face swap model and initializes it if necessary.

    Args:
        model_path (str): Path to the face swap model.

    Returns:
        insightface.model_zoo.FaceModel: The face swap model.
    """
    try:
        # Initializes the face swap model using the specified model path.
        return upscaled_inswapper.UpscaledINSwapper(
            insightface.model_zoo.get_model(model_path, providers=providers)
        )
    except Exception as e:
        logger.error(
            "Loading of swapping model failed, please check the requirements (On Windows, download and install Visual Studio. During the install, make sure to include the Python and C++ packages.)"
        )
        raise FaceModelException("Loading of swapping model failed")


def get_faces(
    img_data: np.ndarray,  # type: ignore
    det_size: Tuple[int, int] = (640, 640),
    det_thresh: Optional[float] = None,
    sort_by_face_size: bool = False,
) -> List[Face]:
    """
    Detects and retrieves faces from an image using an analysis model.

    Args:
        img_data (np.ndarray): The image data as a NumPy array.
        det_size (tuple): The desired detection size (width, height). Defaults to (640, 640).
        sort_by_face_size (bool) : Will sort the faces by their size from larger to smaller face

    Returns:
        list: A list of detected faces, sorted by their x-coordinate of the bounding box.
    """

    if det_thresh is None:
        det_thresh = opts.data.get("faceswaplab_detection_threshold", 0.5)

    # Create a deep copy of the analysis model (otherwise det_size is attached to the analysis model and can't be changed)
    face_analyser = copy.deepcopy(getAnalysisModel())

    # Prepare the analysis model for face detection with the specified detection size
    face_analyser.prepare(ctx_id=0, det_thresh=det_thresh, det_size=det_size)

    # Get the detected faces from the image using the analysis model
    face = face_analyser.get(img_data)

    # If no faces are detected and the detection size is larger than 320x320,
    # recursively call the function with a smaller detection size
    if len(face) == 0 and det_size[0] > 320 and det_size[1] > 320:
        det_size_half = (det_size[0] // 2, det_size[1] // 2)
        return get_faces(img_data, det_size=det_size_half, det_thresh=det_thresh)

    try:
        if sort_by_face_size:
            return sorted(
                face,
                reverse=True,
                key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]),
            )

        # Sort the detected faces based on their x-coordinate of the bounding box
        return sorted(face, key=lambda x: x.bbox[0])
    except Exception as e:
        return []


@dataclass
class ImageResult:
    """
    Represents the result of an image swap operation
    """

    image: Image.Image
    """
    The image object with the swapped face
    """

    similarity: Dict[int, float]
    """
    A dictionary mapping face indices to their similarity scores.
    The similarity scores are represented as floating-point values between 0 and 1.
    """

    ref_similarity: Dict[int, float]
    """
    A dictionary mapping face indices to their similarity scores compared to a reference image.
    The similarity scores are represented as floating-point values between 0 and 1.
    """


def get_or_default(l: List[Any], index: int, default: Any) -> Any:
    """
    Retrieve the value at the specified index from the given list.
    If the index is out of bounds, return the default value instead.

    Args:
        l (list): The input list.
        index (int): The index to retrieve the value from.
        default: The default value to return if the index is out of bounds.

    Returns:
        The value at the specified index if it exists, otherwise the default value.
    """
    return l[index] if index < len(l) else default


import gradio as gr


def get_faces_from_img_files(files: List[gr.File]) -> List[Optional[np.ndarray]]:  # type: ignore
    """
    Extracts faces from a list of image files.

    Args:
        files (list): A list of file objects representing image files.

    Returns:
        list: A list of detected faces.

    """

    faces = []

    if len(files) > 0:
        for file in files:
            img = Image.open(file.name)  # Open the image file
            face = get_or_default(
                get_faces(pil_to_cv2(img)), 0, None
            )  # Extract faces from the image
            if face is not None:
                faces.append(face)  # Add the detected face to the list of faces

    return faces


def blend_faces(faces: List[Face]) -> Face:
    """
    Blends the embeddings of multiple faces into a single face.

    Args:
        faces (List[Face]): List of Face objects.

    Returns:
        Face: The blended Face object with the averaged embedding.
              Returns None if the input list is empty.

    Raises:
        ValueError: If the embeddings have different shapes.

    """
    embeddings = [face.embedding for face in faces]

    if len(embeddings) > 0:
        embedding_shape = embeddings[0].shape

        # Check if all embeddings have the same shape
        for embedding in embeddings:
            if embedding.shape != embedding_shape:
                raise ValueError("embedding shape mismatch")

        # Compute the mean of all embeddings
        blended_embedding = np.mean(embeddings, axis=0)

        # Create a new Face object using the properties of the first face in the list
        # Assign the blended embedding to the blended Face object
        blended = Face(
            embedding=blended_embedding, gender=faces[0].gender, age=faces[0].age
        )

        assert (
            not np.array_equal(blended.embedding, faces[0].embedding)
            if len(faces) > 1
            else True
        ), "If len(faces)>0, the blended embedding should not be the same than the first image"

        return blended

    # Return None if the input list is empty
    return None


def swap_face(
    reference_face: np.ndarray,  # type: ignore
    source_face: np.ndarray,  # type: ignore
    target_img: Image.Image,
    model: str,
    faces_index: Set[int] = {0},
    same_gender: bool = True,
    upscaled_swapper: bool = False,
    compute_similarity: bool = True,
    sort_by_face_size: bool = False,
) -> ImageResult:
    """
    Swaps faces in the target image with the source face.

    Args:
        reference_face (np.ndarray): The reference face used for similarity comparison.
        source_face (np.ndarray): The source face to be swapped.
        target_img (Image.Image): The target image to swap faces in.
        model (str): Path to the face swap model.
        faces_index (Set[int], optional): Set of indices specifying which faces to swap. Defaults to {0}.
        same_gender (bool, optional): If True, only swap faces with the same gender as the source face. Defaults to True.

    Returns:
        ImageResult: An object containing the swapped image and similarity scores.

    """
    return_result = ImageResult(target_img, {}, {})
    try:
        target_img = cv2.cvtColor(np.array(target_img), cv2.COLOR_RGB2BGR)
        gender = source_face["gender"]
        logger.info("Source Gender %s", gender)
        if source_face is not None:
            result = target_img
            model_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), model)
            face_swapper = getFaceSwapModel(model_path)
            target_faces = get_faces(target_img, sort_by_face_size=sort_by_face_size)
            logger.info("Target faces count : %s", len(target_faces))

            if same_gender:
                target_faces = [x for x in target_faces if x["gender"] == gender]
                logger.info("Target Gender Matches count %s", len(target_faces))

            for i, swapped_face in enumerate(target_faces):
                logger.info(f"swap face {i}")
                if i in faces_index:
                    # type : ignore
                    result = face_swapper.get(
                        result, swapped_face, source_face, upscale=upscaled_swapper
                    )

            result_image = Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB))
            return_result.image = result_image

            if compute_similarity:
                try:
                    result_faces = get_faces(
                        cv2.cvtColor(np.array(result_image), cv2.COLOR_RGB2BGR),
                        sort_by_face_size=sort_by_face_size,
                    )
                    if same_gender:
                        result_faces = [
                            x for x in result_faces if x["gender"] == gender
                        ]

                    for i, swapped_face in enumerate(result_faces):
                        logger.info(f"compare face {i}")
                        if i in faces_index and i < len(target_faces):
                            return_result.similarity[i] = cosine_similarity_face(
                                source_face, swapped_face
                            )
                            return_result.ref_similarity[i] = cosine_similarity_face(
                                reference_face, swapped_face
                            )

                        logger.info(f"similarity {return_result.similarity}")
                        logger.info(f"ref similarity {return_result.ref_similarity}")

                except Exception as e:
                    logger.error("Similarity processing failed %s", e)
                    raise e
    except Exception as e:
        logger.error("Conversion failed %s", e)
        raise e
    return return_result


def process_image_unit(
    model: str,
    unit: FaceSwapUnitSettings,
    image: Image.Image,
    info: str = None,
    upscaled_swapper: bool = False,
    force_blend: bool = False,
) -> List[Tuple[Image.Image, str]]:
    """Process one image and return a List of (image, info) (one if blended, many if not).

    Args:
        unit : the current unit
        image : the image where to apply swapping
        info : The info

    Returns:
        List of tuple of (image, info) where image is the image where swapping has been applied and info is the image info with similarity infos.
    """

    results = []
    if unit.enable:
        if check_against_nsfw(image):
            return [(image, info)]
        if not unit.blend_faces and not force_blend:
            src_faces = unit.faces
            logger.info(f"will generate {len(src_faces)} images")
        else:
            logger.info("blend all faces together")
            src_faces = [unit.blended_faces]
            assert (
                not np.array_equal(
                    unit.reference_face.embedding, src_faces[0].embedding
                )
                if len(unit.faces) > 1
                else True
            ), "Reference face cannot be the same as blended"

        for i, src_face in enumerate(src_faces):
            logger.info(f"Process face {i}")
            if unit.reference_face is not None:
                reference_face = unit.reference_face
            else:
                logger.info("Use source face as reference face")
                reference_face = src_face

            save_img_debug(image, "Before swap")
            result: ImageResult = swap_face(
                reference_face,
                src_face,
                image,
                faces_index=unit.faces_index,
                model=model,
                same_gender=unit.same_gender,
                upscaled_swapper=upscaled_swapper,
                compute_similarity=unit.compute_similarity,
                sort_by_face_size=unit.sort_by_size,
            )
            save_img_debug(result.image, "After swap")

            if result.image is None:
                logger.error("Result image is None")
            if (
                (not unit.check_similarity)
                or result.similarity
                and all(
                    [result.similarity.values() != 0]
                    + [x >= unit.min_sim for x in result.similarity.values()]
                )
                and all(
                    [result.ref_similarity.values() != 0]
                    + [x >= unit.min_ref_sim for x in result.ref_similarity.values()]
                )
            ):
                results.append(
                    (
                        result.image,
                        f"{info}, similarity = {result.similarity}, ref_similarity = {result.ref_similarity}",
                    )
                )
            else:
                logger.warning(
                    f"skip, similarity to low, sim = {result.similarity} (target {unit.min_sim}) ref sim = {result.ref_similarity} (target = {unit.min_ref_sim})"
                )
    logger.debug("process_image_unit : Unit produced %s results", len(results))
    return results


def process_images_units(
    model: str,
    units: List[FaceSwapUnitSettings],
    images: List[Tuple[Optional[Image.Image], Optional[str]]],
    upscaled_swapper: bool = False,
    force_blend: bool = False,
) -> Optional[List[Tuple[Image.Image, str]]]:
    if len(units) == 0:
        logger.info("Finished processing image, return %s images", len(images))
        return None

    logger.debug("%s more units", len(units))

    processed_images = []
    for i, (image, info) in enumerate(images):
        logger.debug("Processing image %s", i)
        swapped = process_image_unit(
            model, units[0], image, info, upscaled_swapper, force_blend
        )
        logger.debug("Image %s -> %s images", i, len(swapped))
        nexts = process_images_units(
            model, units[1:], swapped, upscaled_swapper, force_blend
        )
        if nexts:
            processed_images.extend(nexts)
        else:
            processed_images.extend(swapped)

    return processed_images
