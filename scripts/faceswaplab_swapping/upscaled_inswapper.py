
import cv2
import numpy as np
import onnx
import onnxruntime
from insightface.model_zoo.inswapper import INSwapper
from insightface.utils import face_align
from modules import codeformer_model, processing, scripts, shared
from modules.face_restoration import FaceRestoration
from modules.shared import cmd_opts, opts, state
from modules.upscaler import UpscalerData
from onnx import numpy_helper
from PIL import Image

from scripts.faceswaplab_utils.faceswaplab_logging import logger
from scripts.faceswaplab_postprocessing import upscaling
from scripts.faceswaplab_postprocessing.postprocessing_options import \
    PostProcessingOptions
from scripts.faceswaplab_swapping.facemask import generate_face_mask
from scripts.faceswaplab_utils.imgutils import cv2_to_pil, pil_to_cv2


def get_upscaler() -> UpscalerData:
    for upscaler in shared.sd_upscalers:
        if upscaler.name == opts.data.get("faceswaplab_upscaled_swapper_upscaler", "LDSR"):
            return upscaler
    return None

def merge_images_with_mask(image1, image2, mask):
    if image1.shape != image2.shape or image1.shape[:2] != mask.shape:
        raise ValueError("Img should have the same shape")
    mask = mask.astype(np.uint8)
    masked_region = cv2.bitwise_and(image2, image2, mask=mask)
    inverse_mask = cv2.bitwise_not(mask)
    empty_region = cv2.bitwise_and(image1, image1, mask=inverse_mask)
    merged_image = cv2.add(empty_region, masked_region)
    return merged_image

def erode_mask(mask, kernel_size=3, iterations=1):
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    eroded_mask = cv2.erode(mask, kernel, iterations=iterations)
    return eroded_mask

def apply_gaussian_blur(mask, kernel_size=(5, 5), sigma_x=0):
    blurred_mask = cv2.GaussianBlur(mask, kernel_size, sigma_x)
    return blurred_mask

def dilate_mask(mask, kernel_size=5, iterations=1):
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    dilated_mask = cv2.dilate(mask, kernel, iterations=iterations)
    return dilated_mask

def get_face_mask(aimg,bgr_fake):
    mask1 = generate_face_mask(aimg, device = shared.device)
    mask2 = generate_face_mask(bgr_fake, device = shared.device)
    mask = dilate_mask(cv2.bitwise_or(mask1,mask2))    
    return mask


class UpscaledINSwapper():
    def __init__(self, inswapper : INSwapper):
        self.__dict__.update(inswapper.__dict__)

    def forward(self, img, latent):
        img = (img - self.input_mean) / self.input_std
        pred = self.session.run(self.output_names, {self.input_names[0]: img, self.input_names[1]: latent})[0]
        return pred

    def super_resolution(self,img, k = 2) :
        pil_img = cv2_to_pil(img)
        options = PostProcessingOptions(
            upscaler_name=opts.data.get('faceswaplab_upscaled_swapper_upscaler', 'LDSR'),
            upscale_visibility=1,
            scale=k,
            face_restorer_name=opts.data.get('faceswaplab_upscaled_swapper_face_restorer', ""),
            codeformer_weight= opts.data.get('faceswaplab_upscaled_swapper_face_restorer_weight', 1),
            restorer_visibility=opts.data.get('faceswaplab_upscaled_swapper_face_restorer_visibility', 1))
        upscaled = upscaling.upscale_img(pil_img, options)
        upscaled = upscaling.restore_face(upscaled, options)
        return pil_to_cv2(upscaled)

    def get(self, img, target_face, source_face, paste_back=True, upscale = True):
        aimg, M = face_align.norm_crop2(img, target_face.kps, self.input_size[0])
        blob = cv2.dnn.blobFromImage(aimg, 1.0 / self.input_std, self.input_size,
                                      (self.input_mean, self.input_mean, self.input_mean), swapRB=True)
        latent = source_face.normed_embedding.reshape((1,-1))
        latent = np.dot(latent, self.emap)
        latent /= np.linalg.norm(latent)
        pred = self.session.run(self.output_names, {self.input_names[0]: blob, self.input_names[1]: latent})[0]
        #print(latent.shape, latent.dtype, pred.shape)
        img_fake = pred.transpose((0,2,3,1))[0]
        bgr_fake = np.clip(255 * img_fake, 0, 255).astype(np.uint8)[:,:,::-1]
        
        try :
            if not paste_back:
                return bgr_fake, M
            else:
                target_img = img

                def compute_diff(bgr_fake,aimg) :
                    fake_diff = bgr_fake.astype(np.float32) - aimg.astype(np.float32)
                    fake_diff = np.abs(fake_diff).mean(axis=2)
                    fake_diff[:2,:] = 0
                    fake_diff[-2:,:] = 0
                    fake_diff[:,:2] = 0
                    fake_diff[:,-2:] = 0
                    return fake_diff

                if upscale :

                    print("*"*80)
                    print(f"Upscaled inswapper using {opts.data.get('faceswaplab_upscaled_swapper_upscaler', 'LDSR')}")
                    print("*"*80)
                                    
                    k = 4
                    aimg, M = face_align.norm_crop2(img, target_face.kps, self.input_size[0]*k)                
                   
                    # upscale and restore face :
                    bgr_fake = self.super_resolution(bgr_fake, k)
                    
                    if opts.data.get("faceswaplab_upscaled_improved_mask", True) :
                        mask = get_face_mask(aimg,bgr_fake)
                        bgr_fake = merge_images_with_mask(aimg, bgr_fake,mask)

                    # compute fake_diff before sharpen and color correction (better result)
                    fake_diff = compute_diff(bgr_fake, aimg)

                    if opts.data.get("faceswaplab_upscaled_swapper_sharpen", True) :
                        print("sharpen")
                        # Add sharpness
                        blurred = cv2.GaussianBlur(bgr_fake, (0, 0), 3)
                        bgr_fake = cv2.addWeighted(bgr_fake, 1.5, blurred, -0.5, 0)

                    # Apply color corrections
                    if opts.data.get("faceswaplab_upscaled_swapper_fixcolor", True) :
                        print("color correction")
                        correction = processing.setup_color_correction(cv2_to_pil(aimg))
                        bgr_fake_pil = processing.apply_color_correction(correction, cv2_to_pil(bgr_fake))
                        bgr_fake = pil_to_cv2(bgr_fake_pil)


                else :
                    fake_diff = compute_diff(bgr_fake, aimg)

                IM = cv2.invertAffineTransform(M)

                img_white = np.full((aimg.shape[0],aimg.shape[1]), 255, dtype=np.float32)
                bgr_fake = cv2.warpAffine(bgr_fake, IM, (target_img.shape[1], target_img.shape[0]), borderValue=0.0)
                img_white = cv2.warpAffine(img_white, IM, (target_img.shape[1], target_img.shape[0]), borderValue=0.0)
                fake_diff = cv2.warpAffine(fake_diff, IM, (target_img.shape[1], target_img.shape[0]), borderValue=0.0)
                img_white[img_white>20] = 255
                fthresh = opts.data.get('faceswaplab_upscaled_swapper_fthresh', 10)
                print("fthresh", fthresh)
                fake_diff[fake_diff<fthresh] = 0
                fake_diff[fake_diff>=fthresh] = 255
                img_mask = img_white
                mask_h_inds, mask_w_inds = np.where(img_mask==255)
                mask_h = np.max(mask_h_inds) - np.min(mask_h_inds)
                mask_w = np.max(mask_w_inds) - np.min(mask_w_inds)
                mask_size = int(np.sqrt(mask_h*mask_w))
                erosion_factor = opts.data.get('faceswaplab_upscaled_swapper_erosion', 1)
                k = max(int(mask_size//10*erosion_factor), int(10*erosion_factor))

                kernel = np.ones((k,k),np.uint8)
                img_mask = cv2.erode(img_mask,kernel,iterations = 1)
                kernel = np.ones((2,2),np.uint8)
                fake_diff = cv2.dilate(fake_diff,kernel,iterations = 1)
                k = max(int(mask_size//20*erosion_factor), int(5*erosion_factor))


                kernel_size = (k, k)
                blur_size = tuple(2*i+1 for i in kernel_size)
                img_mask = cv2.GaussianBlur(img_mask, blur_size, 0)
                k = int(5*erosion_factor)
                kernel_size = (k, k)
                blur_size = tuple(2*i+1 for i in kernel_size)
                fake_diff = cv2.GaussianBlur(fake_diff, blur_size, 0)
                img_mask /= 255
                fake_diff /= 255

                img_mask = np.reshape(img_mask, [img_mask.shape[0],img_mask.shape[1],1])
                fake_merged = img_mask * bgr_fake + (1-img_mask) * target_img.astype(np.float32)
                fake_merged = fake_merged.astype(np.uint8)
                return fake_merged
        except Exception as e :
            import traceback
            traceback.print_exc()
            raise e
