# Hardware fit

The **Hardware fit** screen (`GET /api/v1/hardware-fit`) detects this machine and grades each
model in the [model catalog](model-catalog.md) for it. It is loopback-only and read-only; it
downloads nothing. `?refresh=true` runs hardware detection again instead of using the saved
machine profile.

## Memory the model can use

- **Discrete GPU** (NVIDIA, AMD or Intel with dedicated memory): 85% of the largest GPU's memory.
  System RAM minus 6 GB is the overflow pool.
- **Apple silicon**: 75% of unified memory.
- **Otherwise** (including an integrated GPU, which shares RAM and adds no memory): system RAM
  minus 6 GB.

## Per model

For each quantization (Q8_0 8.5, Q6_K 6.56, Q5_K_M 5.69, Q4_K_M 4.85, Q3_K_M 3.91, Q2_K 2.96 bits
per weight) the catalog's 4-bit size is scaled, plus 10% and 0.5 GB for the context cache and
runtime. The model runs **in GPU memory** if that fits, **in GPU memory plus system RAM** if it
only fits with overflow, **on the CPU** without a GPU, or not at all.

Grades:

| Grade | Meaning |
| --- | --- |
| Perfect fit | Within 70% of GPU memory, and at least 10 tokens/s. |
| Good fit | Fits its memory budget and runs at 5 tokens/s or more. |
| Marginal | Needs system RAM as overflow, is estimated under 5 tokens/s, or barely fits. |
| Too large | Does not fit even at Q2_K. Shown at the smallest quantization, not recommended. |

The best-quality quantization with the best grade is chosen. Quantizations below Q4 are used only
when nothing at Q4 or above fits, and are labelled "Lower quality". When a model only just runs
(Marginal), the smallest usable quantization is shown, since that is the one worth downloading.

## Speed

Same method as model suggestions: tokens/s is about the memory read speed divided by the bytes
read per token. The read speed is measured from your completed runs when you have them, or comes
from a known discrete GPU's published bandwidth. With neither, speed is shown as unknown, and the
grade assumes typical memory speed (20 GiB/s) so a large model is not called good on a CPU
only because it fits. Benchmark a model to replace the estimate with a measurement.
