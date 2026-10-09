from .arm_a import ArmA
from .arm_b import ArmB
from .base import Arm, Prediction, read_log, write_log
from .baselines import ArmDrift, ArmSingle

#: Every arm in the trial. A and B are the architectures under test; "drift"
#: and "single" are the baselines they must beat to be worth running at all.
ARMS: dict[str, type[Arm]] = {
    "A": ArmA,
    "B": ArmB,
    "drift": ArmDrift,
    "single": ArmSingle,
}

#: The two arms the trial is actually choosing between.
CONTENDERS = ("A", "B")

__all__ = [
    "ARMS", "CONTENDERS", "Arm", "ArmA", "ArmB", "ArmDrift", "ArmSingle",
    "Prediction", "read_log", "write_log",
]
