"""
FVscheme Module

This module contains finite volume schemes and loss functions for CFD simulations.
It provides both physics-based loss functions (Intergrator classes) and data-driven
loss functions.

NOTE: This module is primarily for physics-based mode. For data-driven mode,
      use LpLoss directly from .dataloss without the config system.
"""


# Loss configuration classes have been removed - config.loss.py was deleted
# For data-driven mode: use LpLoss directly from .dataloss
# For physics-based mode: use the Intergrator classes directly from respective modules
# This module is kept for backward compatibility but the create_loss() function has been removed

# Export main components (empty for now as config-based loss creation is removed)
__all__ = []
