from __future__ import annotations

from statistics import mean

from ..models import PremarketSignal, ReplayEvaluation, SessionDecision


class ReplayEvaluator:
    def evaluate(
        self,
        premarket_signals: list[PremarketSignal],
        decisions: list[SessionDecision],
        top_k: int = 3,
    ) -> tuple[ReplayEvaluation, dict[str, str]]:
        premarket_by_symbol = {signal.symbol: signal for signal in premarket_signals}
        labels: dict[str, str] = {}
        pnl_values: list[float] = []
        winning_pnl: list[float] = []
        losing_pnl: list[float] = []

        for decision in decisions:
            signal = premarket_by_symbol.get(decision.symbol)
            if signal is None:
                continue

            if decision.decision == "ENTER" and decision.regular_close > signal.premarket_high:
                label = "continuation"
            elif decision.regular_high > signal.premarket_high and decision.regular_close < decision.anchored_vwap:
                label = "fakeout"
            else:
                label = "fade"
            labels[decision.symbol] = label

            pnl = decision.mfe_pct if decision.decision == "ENTER" else decision.mae_pct
            pnl_values.append(pnl)
            if pnl >= 0:
                winning_pnl.append(pnl)
            else:
                losing_pnl.append(abs(pnl))

        sorted_candidates = sorted(
            premarket_signals, key=lambda item: (-item.quality_score, item.symbol)
        )[:top_k]
        precision_hits = 0
        for signal in sorted_candidates:
            if labels.get(signal.symbol) == "continuation":
                precision_hits += 1
        precision_at_k = precision_hits / max(len(sorted_candidates), 1)

        gross_profit = sum(winning_pnl)
        gross_loss = sum(losing_pnl)
        expectancy = mean(pnl_values) if pnl_values else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float(gross_profit > 0)
        report_label = (
            "continuation"
            if any(label == "continuation" for label in labels.values())
            else "fade"
        )

        report = ReplayEvaluation(
            label=report_label,
            expectancy=expectancy,
            profit_factor=profit_factor,
            precision_at_k=precision_at_k,
            average_mfe_pct=mean([decision.mfe_pct for decision in decisions]) if decisions else 0.0,
            average_mae_pct=mean([decision.mae_pct for decision in decisions]) if decisions else 0.0,
            symbol_count=len(decisions),
        )
        return report, labels
