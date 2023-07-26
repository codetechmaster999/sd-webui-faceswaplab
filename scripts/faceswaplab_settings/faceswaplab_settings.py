from scripts.faceswaplab_utils.models_utils import get_models
from modules import script_callbacks, shared
import gradio as gr

def on_ui_settings():
    section = ('faceswaplab', "FaceSwapLab")
    models = get_models()
    shared.opts.add_option("faceswaplab_model", shared.OptionInfo(
        models[0] if len(models) > 0 else "None",  "FaceSwapLab FaceSwap Model", gr.Dropdown, {"interactive": True, "choices" : models}, section=section)) 
    shared.opts.add_option("faceswaplab_keep_original", shared.OptionInfo(
        False, "keep original image before swapping", gr.Checkbox, {"interactive": True}, section=section))               
    shared.opts.add_option("faceswaplab_units_count", shared.OptionInfo(
        3, "Max faces units (requires restart)", gr.Slider, {"minimum": 1, "maximum": 10, "step": 1}, section=section))

    shared.opts.add_option("faceswaplab_detection_threshold", shared.OptionInfo(
        0.5, "Detection threshold ", gr.Slider, {"minimum": 0.1, "maximum": 0.99, "step": 0.001}, section=section))



    shared.opts.add_option("faceswaplab_pp_default_face_restorer", shared.OptionInfo(
        None, "UI Default post processing face restorer (requires restart)", gr.Dropdown, {"interactive": True, "choices" : ["None"] + [x.name() for x in shared.face_restorers]}, section=section))
    shared.opts.add_option("faceswaplab_pp_default_face_restorer_visibility", shared.OptionInfo(
        1, "UI Default post processing face restorer visibility (requires restart)", gr.Slider, {"minimum": 0, "maximum": 1, "step": 0.001}, section=section))
    shared.opts.add_option("faceswaplab_pp_default_face_restorer_weight", shared.OptionInfo(
        1, "UI Default post processing face restorer weight (requires restart)", gr.Slider, {"minimum": 0, "maximum": 1, "step": 0.001}, section=section))  
    shared.opts.add_option("faceswaplab_pp_default_upscaler", shared.OptionInfo(
        None, "UI Default post processing upscaler (requires restart)", gr.Dropdown, {"interactive": True, "choices" : [upscaler.name for upscaler in shared.sd_upscalers]}, section=section))
    shared.opts.add_option("faceswaplab_pp_default_upscaler_visibility", shared.OptionInfo(
        1, "UI Default post processing upscaler visibility(requires restart)", gr.Slider, {"minimum": 0, "maximum": 1, "step": 0.001}, section=section))


    shared.opts.add_option("faceswaplab_upscaled_swapper", shared.OptionInfo(
        False, "Upscaled swapper. Applied only to the swapped faces. Apply transformations before merging with the original image.", gr.Checkbox, {"interactive": True}, section=section))
    shared.opts.add_option("faceswaplab_upscaled_swapper_upscaler", shared.OptionInfo(
        None, "Upscaled swapper upscaler (Recommanded : LDSR but slow)", gr.Dropdown, {"interactive": True, "choices" : [upscaler.name for upscaler in shared.sd_upscalers]}, section=section))
    shared.opts.add_option("faceswaplab_upscaled_swapper_sharpen", shared.OptionInfo(
        False, "Upscaled swapper sharpen", gr.Checkbox, {"interactive": True}, section=section))
    shared.opts.add_option("faceswaplab_upscaled_swapper_fixcolor", shared.OptionInfo(
        False, "Upscaled swapper color correction", gr.Checkbox, {"interactive": True}, section=section))    
    shared.opts.add_option("faceswaplab_upscaled_improved_mask", shared.OptionInfo(
        True, "Use improved segmented mask (use pastenet to mask only the face)", gr.Checkbox, {"interactive": True}, section=section))     
    shared.opts.add_option("faceswaplab_upscaled_swapper_face_restorer", shared.OptionInfo(
        None, "Upscaled swapper face restorer", gr.Dropdown, {"interactive": True, "choices" : ["None"] + [x.name() for x in shared.face_restorers]}, section=section))
    shared.opts.add_option("faceswaplab_upscaled_swapper_face_restorer_visibility", shared.OptionInfo(
        1, "Upscaled swapper face restorer visibility", gr.Slider, {"minimum": 0, "maximum": 1, "step": 0.001}, section=section))
    shared.opts.add_option("faceswaplab_upscaled_swapper_face_restorer_weight", shared.OptionInfo(
        1, "Upscaled swapper face restorer weight (codeformer)", gr.Slider, {"minimum": 0, "maximum": 1, "step": 0.001}, section=section))
    shared.opts.add_option("faceswaplab_upscaled_swapper_fthresh", shared.OptionInfo(
        10, "Upscaled swapper fthresh (diff sensitivity) 10 = default behaviour. Low impact.", gr.Slider, {"minimum": 5, "maximum": 250, "step": 1}, section=section))
    shared.opts.add_option("faceswaplab_upscaled_swapper_erosion", shared.OptionInfo(
        1, "Upscaled swapper mask erosion factor, 1 = default behaviour. The larger it is, the more blur is applied around the face. Too large and the facial change is no longer visible.", gr.Slider, {"minimum": 0, "maximum": 10, "step": 0.001}, section=section))

script_callbacks.on_ui_settings(on_ui_settings)