import sys; sys.path.insert(0,'.')
from amo_human_state import HumanState
print('has scene constrain:', hasattr(HumanState, '_jit_scene_constrain'))
print('has jit plan:', hasattr(HumanState,'_jit_plan_for_hour'))
