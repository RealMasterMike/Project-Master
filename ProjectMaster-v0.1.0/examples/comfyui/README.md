# ComfyUI example workflows

## Audited default policy

Project Master seeds only workflows whose publishers explicitly document the
underlying model or adapter as uncensored, NSFW-capable, or supporting both SFW
and NSFW output. Local execution alone is not treated as proof. Other examples
in this directory remain available for manual compatibility testing, but are
not surfaced as preferred defaults.

All physical smoke tests use benign prompts. They verify installation,
workflow structure, hardware fit, artifact import, and project isolation; they
do not attempt to benchmark restricted or explicit content.

## Chroma1-Flash Uncensored (automatic still-image default)

Chroma is the automatic Text-to-Image and Image-to-Image default as of
2026-07-28:

- `chroma1-flash-uncensored-text-to-image-project-master-import.json`
- `chroma1-flash-uncensored-image-to-image-project-master-import.json`

Unlike a third-party finetune of a filtered base model, Chroma was retrained on
an unfiltered dataset by its own publisher, who states this directly:

> "Chroma: Open-Source, Uncensored, and Built for the Community"
> "**Fully uncensored**, reintroducing missing anatomical concepts."
> "Training on a **5M dataset**, curated from **20M** samples including anime,
> furry, artistic stuff, and photos."

The final release card adds: "The model is released in a state as is and has not
been aligned with a specific safety filter." The model is 8.9B parameters, based
on FLUX.1-schnell, and Apache-2.0 licensed.

Required local files:

| ComfyUI directory | File | SHA-256 |
| --- | --- | --- |
| `models/unet/` | `Chroma1-HD-Flash-Q4_K_M.gguf` | see `Chroma1-HD-Q4_K_M.PROVENANCE.md` |
| `models/text_encoders/` | `t5-v1_1-xxl-encoder-Q5_K_M.gguf` | `b51cbb10b1a7aac6dd1c3b62f0ed908bfd06e0b42d2f3577d43e061361f51dae` |
| `models/vae/` | `flux-ae.safetensors` | `afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38` |

Sources:

- `https://huggingface.co/lodestones/Chroma1-HD` (project and final release card)
- `https://huggingface.co/silveroxides/Chroma1-Flash-GGUF` (quantized model)
- `https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf` (text encoder)
- `https://huggingface.co/Comfy-Org/Lumina_Image_2.0_Repackaged` (FLUX autoencoder;
  the upstream `black-forest-labs/FLUX.1-schnell` copy is gated)

Chroma is **not** a CFG-distilled model. It takes a real negative prompt through
`CFGGuider`. At guidance `1.0` the negative prompt has no effect and generation
is roughly twice as fast, which is why the binding description says so rather
than presenting an inert control. The graph uses `ModelSamplingAuraFlow` shift
1.0, `T5TokenizerOptions` min_padding/min_length 0, `euler`, `beta`, and
`EmptySD3LatentImage`, matching the official ComfyUI `image_chroma_text_to_image`
template. ComfyUI 0.28.0 supports `chroma` natively; only the existing
`ComfyUI-GGUF` node is additionally required.

Measured on an 8 GB RTX 5070 Laptop GPU at 1024x1024, benign prompts:

| Configuration | Wall time |
| --- | --- |
| Chroma1-Flash, 10 steps, guidance 2.5 | 93 s |
| Chroma1-Flash, 10 steps, guidance 1.0 | 58 s |
| Chroma1-HD (non-distilled), 26 steps, guidance 3.5 | 266 s |

The distilled Flash model is used as the default because it was both faster and
visibly sharper than Chroma1-HD in side-by-side benign smoke output.

## RealVisXL V5.0 SFW + NSFW

RealVisXL remains a curated default and stays selectable, but it is no longer
the automatic choice. Its provenance is unchanged and it is **not**
manual/unverified:

- `realvisxl-v5-nsfw-capable-text-to-image-project-master-import.json`
- `realvisxl-v5-nsfw-capable-image-to-image-project-master-import.json`

The image-to-image wrapper accepts a verified Creator Media image through its
`image_asset` binding. Project Master uploads a sanitized copy into a fixed
ComfyUI namespace and records durable input provenance without exposing local
filesystem paths.

Required local file:

| ComfyUI directory | File | SHA-256 |
| --- | --- | --- |
| `models/checkpoints/` | `RealVisXL_V5.0_fp16.safetensors` | `6a35a7855770ae9820a3c931d4964c3817b6d9e3c6f9c4dabb5b3a94e5643b80` |

The checkpoint is pinned to repository revision
`ac93e0dda1f6d448cae19bbfab8c5e720a5e48bc`. Its publisher explicitly
describes support for both SFW and NSFW images; the model is licensed under
OpenRAIL++.

Source:

- `https://huggingface.co/SG161222/RealVisXL_V5.0`

A benign 512×512 text-to-image run completed locally. A separate benign
image-to-image smoke then exercised the complete Project Master path with a
fresh isolated Creator project: verified Media upload, compatibility check,
ComfyUI input staging, job polling, artifact import, and Media cataloging. The
job finished in 6.41 seconds; its 386,593-byte PNG artifact and catalog copy
matched at SHA-256
`21e3177a91264897d04997ca7e9bab89f7dbb15fb70b58e9f8cd011a582ea746`.
Repeated terminal refresh retained exactly one generated Media entry.

## Wan 2.2 LightX2V 4-Step Uncensored

`wan2.2-lightx2v-4step-uncensored-api.json` is the preferred local
text-to-video workflow for the publisher-labeled Uncensored stack. It uses the
stock Wan 2.2 high- and low-noise T2V experts with rzgar's matching high/low
LightX2V adapters. `wan2.2-lightx2v-4step-uncensored-project-master-import.json`
wraps the workflow for Creator → Workflows with bounded prompt, seed, size,
frame-count, and FPS bindings.

Required local files:

| ComfyUI directory | File | SHA-256 |
| --- | --- | --- |
| `models/unet/` | `wan2.2_t2v_high_noise_14B_Q3_K_S.gguf` | `0ce18104e45e8e9eae97f2782002b8bee950f24a06acb17a01d5e3c2914dab6b` |
| `models/unet/` | `wan2.2_t2v_low_noise_14B_Q3_K_S.gguf` | `f3da4a55ddc770aab74e37ab8aed377bbd8090deb468a1891bf503a04946b40f` |
| `models/loras/` | `Wan2.2_LightX2V_high_n54vv.safetensors` | `13be3b91290212ada7f9097d7aa1c1354493004952a65794acafa0e9f66122ac` |
| `models/loras/` | `Wan2.2_LightX2V_low_n54vv.safetensors` | `85fb420fdcd219c08fed2432f932179103546ac2c55a463feffecdce1c6a6f5d` |
| `models/text_encoders/` | `umt5-xxl-encoder-Q3_K_M.gguf` | `b7e2ca4c493c9d51fa951005e8ceba2f4b6b6877cfb4c36a8955c6cd68a1dba7` |
| `models/vae/` | `wan_2.1_vae.safetensors` | `2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b` |

The workflow also requires
[`city96/ComfyUI-GGUF`](https://github.com/city96/ComfyUI-GGUF), pinned locally
at commit `6ea2651e7df66d7585f6ffee804b20e92fb38b8a`. The ComfyUI launcher keeps all
custom nodes disabled except this explicit whitelist entry.

Sources:

- Publisher-labeled Uncensored adapter and Apache 2.0 license:
  `https://huggingface.co/rzgar/Wan2.2_LightX2V_4Step_Uncensored`
- Quantized Wan 2.2 T2V bases and Apache 2.0 license:
  `https://huggingface.co/bullerwins/Wan2.2-T2V-A14B-GGUF`
- Low-memory UMT5 encoder:
  `https://huggingface.co/city96/umt5-xxl-encoder-gguf`
- Official dual-expert workflow structure:
  `https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_t2v.json`

The installed Q3 stack was physically verified with a benign 384×224,
9-frame, 4-step run on the local 8 GB GPU. It completed both expert stages,
produced a valid H.264 MP4, and then completed the same job and artifact-import
path through Project Master. That smoke test validates plumbing and hardware
fit; it is not a quality or explicit-content benchmark. ComfyUI-GGUF describes
LoRA patching as experimental, so retain the exact pinned files and hashes.

## Wan 2.2 LightX2V 4-Step Uncensored image to video

`wan2.2-lightx2v-4step-uncensored-image-to-video-project-master-import.json`
is the preferred local image-to-video workflow. The same rzgar publisher card
explicitly documents its high/low LightX2V adapter pair for both I2V and T2V.
The wrapper accepts one verified Creator Media image, uses Wan's core
`WanImageToVideo` conditioning node, and keeps the adapters and dual-expert
four-step sampling structure aligned with the verified text-to-video stack.

Additional required local files:

| ComfyUI directory | File | SHA-256 |
| --- | --- | --- |
| `models/unet/` | `Wan2.2-I2V-A14B-HighNoise-Q3_K_S.gguf` | `2708962c357537c9f517fa49edd8397f3024057b059c3e8df827c774271e1161` |
| `models/unet/` | `Wan2.2-I2V-A14B-LowNoise-Q3_K_S.gguf` | `3352289be6021c783df4716686fb3bb8ec09bf8e1230350145294c78d1ce55b0` |

Both files come from `QuantStack/Wan2.2-I2V-A14B-GGUF`, pinned at immutable
revision `9794085c83d483942dc581389825faa8b09f7592`. They are Apache 2.0
quantizations of the matching Wan I2V high- and low-noise experts.

Sources:

- Publisher-labeled Uncensored I2V/T2V adapters:
  `https://huggingface.co/rzgar/Wan2.2_LightX2V_4Step_Uncensored`
- Quantized Wan 2.2 I2V bases:
  `https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF`

The wrapper defaults to 512×288 and 33 frames. The installed stack was
physically verified with a benign 384×224, 9-frame, four-step direct run. It
completed in 321.484 seconds and produced a valid 15,612-byte H.264 MP4 with
SHA-256
`c6fa25a34c643eaba496f1c3512423bf45644cdff3817c0491260151f3082068`.
An isolated Project Master Creator run then exercised verified Media input,
compatibility preflight, sanitized ComfyUI staging, durable input provenance,
job polling, artifact import, and generated Media cataloging. It reached
`succeeded` with artifact status `ready` in 20.110 seconds; the app-owned
artifact and Media copy were byte-identical 16,075-byte MP4s with SHA-256
`9f80d4bffa90bf0e1be7d9db4f174a2b39ffa0f2417944ce05710441205a8a82`.
Both videos decoded as 384×224 H.264 at 12 fps with exactly 9 frames, and
visual contact sheets showed coherent source preservation and benign steam
motion. This validates installation, plumbing, and hardware fit, not output
quality or explicit-content behavior.

The workflow remains unavailable when either base expert is missing. The
installation procedure verifies each download against the exact hash above
before replacing its `.partial` file; ComfyUI's runtime compatibility API
reports filenames, not file hashes.

## Wan 2.2 Rapid Mega v12.1 NSFW fallback

`wan2.2-rapid-mega-v12.1-nsfw-api.json` and its matching Project Master import
file provide a smaller one-model fallback. The upstream publisher explicitly
labels this checkpoint as an NSFW merge, but “uncensored” is not a standardized
model property and should not be inferred merely from local execution.

Required local files:

| ComfyUI directory | File | SHA-256 |
| --- | --- | --- |
| `models/unet/` | `wan2.2-rapid-mega-aio-nsfw-v12.1-Q3_K.gguf` | `ee37b59361f1454c7187b92ae032567d3ab1aaafa4245a97ddcdf602f594ffb0` |
| `models/text_encoders/` | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68` |
| `models/vae/` | `wan_2.1_vae.safetensors` | `2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b` |

Sources:

- Source NSFW merge:
  `https://huggingface.co/Phr00t/WAN2.2-14B-Rapid-AllInOne`
- GGUF quantization:
  `https://huggingface.co/befox/WAN2.2-14B-Rapid-AllInOne-GGUF`

The source publisher now marks this model family as deprecated. It remains
installed as a physically verified, lower-complexity fallback, not as the
preferred workflow.

## Wan 2.2 TI2V 5B

`wan2.2-ti2v-5b-api.json` is an API-format, core-node-only text-to-video workflow derived from
Comfy-Org's official Wan 2.2 TI2V 5B template. It saves an H.264 MP4 and is suitable for importing
through Creator → Workflows with the purpose set to `video`.

`wan2.2-ti2v-5b-project-master-import.json` wraps the same immutable workflow in Project Master's
workflow-import request shape and adds bounded prompt, seed, sampling, size, frame-count, and FPS
bindings.

Required local files:

| ComfyUI directory | File | SHA-256 |
| --- | --- | --- |
| `models/diffusion_models/` | `wan2.2_ti2v_5B_fp16.safetensors` | `456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e` |
| `models/text_encoders/` | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68` |
| `models/vae/` | `wan2.2_vae.safetensors` | `e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156` |

Sources:

- Official workflow:
  `https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_5B_ti2v.json`
- ComfyUI guide: `https://docs.comfy.org/tutorials/video/wan/wan2_2`
- ComfyUI-packaged weights:
  `https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged`
- Wan model card and Apache 2.0 license:
  `https://huggingface.co/Wan-AI/Wan2.2-TI2V-5B`

The official template targets 1280×704 and 121 frames. This Project Master example defaults to
640×352 and 49 frames so the first local run is more conservative on an 8 GB GPU. Small smoke-test
sizes prove plumbing but should not be used to judge the model's intended 720p quality.
