# WhisperKit models

`whisperkit-cli` runs CoreML weights from
`https://huggingface.co/argmaxinc/whisperkit-coreml`. That repository holds
every Whisper variant. Download one folder, not the tree.

## Picks for a Mac

| Folder | Disk | Notes |
| --- | --- | --- |
| `openai_whisper-large-v3-v20240930_turbo` | 1.5 GB | Default. Fast decoder, large-v3 accuracy. |
| `openai_whisper-large-v3-v20240930_626MB` | about 630 MB | Compressed full decoder. Best on accents, crosstalk, or low volume. |
| `openai_whisper-tiny` | about 77 MB | Smoke test only. Poor accuracy. |

Other folders exist (`distil-whisper_*`, `openai_whisper-small`, and more).
Use them only when someone asks.

## How the name is passed

`whisperkit-cli` builds the search name from `--model-prefix` and `--model`.
The wrapper strips the publisher prefix, so both of these work:

```bash
--model openai_whisper-large-v3-v20240930_turbo
--model large-v3-v20240930_turbo
```

## Cache

First use downloads two things:

```text
~/Documents/huggingface/models/argmaxinc/whisperkit-coreml/<model folder>/
~/Documents/huggingface/models/openai/whisper-large-v3/          (tokenizer, 3 MB)
```

`--cache-dir /some/base` changes the base. The layout below it stays
`models/argmaxinc/whisperkit-coreml/<model folder>/`.

A machine with no network needs both, and it must use `--model-path`.
`--model` contacts Hugging Face on every run to resolve the name, even when
the model is already cached. Verified with the network denied: `--model`
fails with `downloadError`, `--model-path` transcribes.

Check what is already on the machine:

```bash
ls ~/Documents/huggingface/models/argmaxinc/whisperkit-coreml
```

## Prefetch one model

Use this on a metered or offline machine. It needs `git-lfs`.

```bash
brew install git-lfs
git lfs install

MODEL=openai_whisper-large-v3-v20240930_turbo
DEST=~/whisperkit-models

git clone --filter=blob:none --no-checkout \
  https://huggingface.co/argmaxinc/whisperkit-coreml "$DEST"
cd "$DEST"
git sparse-checkout set --no-cone "$MODEL"
git checkout main
```

`--filter=blob:none` plus `sparse-checkout` fetches one model folder. A plain
`git clone` of this repository pulls every variant.

Clone into an empty directory of your choosing, not into the cache directory:
`git clone` refuses a path that already exists, and a sparse checkout does not
build the cache metadata that `--model` looks for. Use the result with
`--model-path "$DEST/$MODEL"`.

## Fixed model folder

`--model-path` points at a model folder. It is the only offline path, and the
only way to be sure which weights ran. The tokenizer must already be cached
from an earlier online run:

```bash
python3 "$SKILL_ROOT/scripts/transcribe.py" \
  --input /path/to/audio.wav \
  --out /path/to/transcript.md \
  --model-path ~/Documents/huggingface/models/argmaxinc/whisperkit-coreml/openai_whisper-large-v3-v20240930_turbo
```

## Compute

`whisperkit-cli` defaults both the audio encoder and the text decoder to
`cpuAndNeuralEngine`. Model compilation on the first run of a new model takes
extra time. Later runs load from the CoreML cache.
