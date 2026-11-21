"""The agent's only platform layer: every call goes through ``agent.scripted_api``.

- :mod:`~agent.tools.project`: ``edit_project``, ``query_project``, ``project_identity``, ``validate_project``,
  ``export_liquid``, ``export_air``;
- :mod:`~agent.tools.edits`: semantic edits -> automation-contract build steps;
- :mod:`~agent.tools.evaluate`: ``admit_air``, ``mesh_air``, ``solve_air``, ``evaluate_air``,
  ``evaluate_hybrid``, ``evaluate_liquid``, ``liquid_steady``, ``liquid_gradient``, ``evaluate_many``,
  ``identity``;
- :mod:`~agent.tools.results`: ``read_result`` and the typed views over ``result.json``;
- :mod:`~agent.tools.fidelity`: ``Fidelity``, ``Budget`` and ``fidelity.evaluate`` (budget, costs, run log).

Nothing here imports ``backend.aircooling``/``backend.liquidcooling`` or a
private (underscore) name of the platform.
"""
from agent.tools.edits import (AddProbe, ContractVerbMissing, Edit, MoveModel, ProjectView, RemoveModels,
                               RotateModel, SetAcuSupply, SetCduSupply, SetPipeDiameter, SetPipeRoute, SetPumpSpeed,
                               SetRackPower, SetServerLoad, SetValveOpening, compile_edits, edit_from_dict)
from agent.tools.evaluate import (admit_air, evaluate_air, evaluate_hybrid, evaluate_liquid, evaluate_many, identity,
                                  liquid_gradient, liquid_steady, mesh_air, solve_air)
from agent.tools.fidelity import Budget, BudgetExhausted, EvaluationRecord, Fidelity
from agent.tools.project import (edit_project, export_air, export_liquid, project_identity, query_project,
                                 validate_project)
from agent.tools.results import read_result, view

__all__ = [
    "edit_project", "query_project", "project_identity", "validate_project", "export_liquid", "export_air",
    "Edit", "MoveModel", "RotateModel", "RemoveModels", "SetServerLoad", "SetRackPower", "SetAcuSupply", "AddProbe",
    "SetCduSupply", "SetPumpSpeed", "SetValveOpening", "SetPipeDiameter", "SetPipeRoute", "ContractVerbMissing",
    "ProjectView",
    "compile_edits", "edit_from_dict",
    "admit_air", "mesh_air", "solve_air", "evaluate_air", "evaluate_liquid", "liquid_steady", "liquid_gradient",
    "evaluate_hybrid", "evaluate_many", "identity", "read_result", "view",
    "Fidelity", "Budget", "BudgetExhausted", "EvaluationRecord",
]
# ``agent.tools.evaluate`` stays the module of evaluation tools; the fidelity dispatcher is
# ``agent.tools.fidelity.evaluate`` (re-exported as ``agent.api.evaluate``).
