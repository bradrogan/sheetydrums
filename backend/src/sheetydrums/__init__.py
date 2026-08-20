import warnings

# Silence a noisy third-party FutureWarning emitted at import time by
# rotary-embedding-torch 0.6.x (pulled in transitively by audio-separator, which
# hard-pins it <0.7.0 so we can't upgrade to the fixed 0.9.1). Its module-level
# `@autocast(enabled=False)` decorators use the deprecated `torch.cuda.amp.autocast`
# and spam:
#   `torch.cuda.amp.autocast(args...)` is deprecated. Please use `torch.amp.autocast('cuda', args...)`.
# Scoped tightly to that exact message so real deprecations still surface. Set
# here (package __init__) because it runs before any submodule pulls in torch.
warnings.filterwarnings(
    "ignore",
    message=r".*torch\.cuda\.amp\.autocast.*is deprecated",
    category=FutureWarning,
)

from sheetydrums.cli import main  # noqa: E402  (must follow the warnings filter)

__all__ = ["main"]
