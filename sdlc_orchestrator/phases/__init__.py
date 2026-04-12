from sdlc_orchestrator.state_machine import State
from .base import PhaseHandler
from .requirement import RequirementDiscoveryHandler, RequirementBuildHandler
from .design import DesignHandler
from .planning import PlanningHandler
from .implementation import ImplementationHandler
from .validation import ValidationHandler
from .feedback import FeedbackHandler
from .review import ReviewHandler

# Maps each executable state to its handler
PHASE_REGISTRY: dict[State, PhaseHandler] = {
    State.DRAFT_REQUIREMENT:            RequirementDiscoveryHandler(),
    State.REQUIREMENT_IN_PROGRESS:      RequirementBuildHandler(),
    State.DESIGN_IN_PROGRESS:           DesignHandler(),
    State.TASK_PLAN_IN_PROGRESS:        PlanningHandler(),
    State.IMPLEMENTATION_IN_PROGRESS:   ImplementationHandler(),
    State.TEST_FAILURE_LOOP:            ValidationHandler(),
    State.FEEDBACK_INCORPORATION:       FeedbackHandler(),
}


def get_handler(state: State) -> PhaseHandler | None:
    return PHASE_REGISTRY.get(state)
