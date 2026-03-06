from __future__ import annotations

from dataclasses import dataclass, field

from runtime import config


@dataclass
class TraceStage:
    stage: str
    gate_type: str
    decision: str
    reason_codes: list[str] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    llm_output_ref: str = ""
    as_of: str = ""

    def to_dict(self) -> dict:
        payload = {
            "stage": self.stage,
            "gate_type": self.gate_type,
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "artifacts": dict(self.artifacts),
            "as_of": self.as_of,
        }
        if self.llm_output_ref:
            payload["llm_output_ref"] = self.llm_output_ref
        return payload


@dataclass
class SymbolTrace:
    session_id: str
    ticker: str
    as_of: str
    stages: list[TraceStage] = field(default_factory=list)
    final_status: str = "pending"

    def add_stage(
        self,
        *,
        stage: str,
        gate_type: str,
        decision: str,
        reason_codes: list[str] | None = None,
        artifacts: dict | None = None,
        llm_output_ref: str = "",
        as_of: str = "",
    ) -> None:
        self.stages.append(
            TraceStage(
                stage=stage,
                gate_type=gate_type,
                decision=decision,
                reason_codes=list(reason_codes or []),
                artifacts=dict(artifacts or {}),
                llm_output_ref=llm_output_ref,
                as_of=as_of or self.as_of,
            )
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": config.PIPELINE_CONFIG["schema_versions"]["trace"],
            "session_id": self.session_id,
            "ticker": self.ticker,
            "as_of": self.as_of,
            "stages": [stage.to_dict() for stage in self.stages],
            "final_status": self.final_status,
        }
