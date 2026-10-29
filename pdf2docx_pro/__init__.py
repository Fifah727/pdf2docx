"""PDF -> DOCX sans Microsoft Word : mise en page conservée, document modifiable."""
from .config import Config
from .converter import convert

__version__ = "0.1.0"
__all__ = ["Config", "convert", "__version__"]
