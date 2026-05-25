/**
 * Client for the reset-button profile cycle.
 *
 * The Slate's reset button (under 3s press) cycles through this ordered
 * list of steps locally. Saving here pushes the new cycle.json to the
 * Slate immediately — no need to also hit /api/agent/sync for it.
 */
import { api } from "@/api/client";

export type CycleStepKind = "profile" | "action";

export interface CycleStep {
  kind: CycleStepKind;
  name: string;
}

export interface ButtonCycleSaveResult {
  steps: CycleStep[];
  pushed_to_slate: boolean;
  push_error: string | null;
}

export async function getButtonCycle(): Promise<{ steps: CycleStep[] }> {
  const { data } = await api.get<{ steps: CycleStep[] }>(
    "/api/settings/button-cycle",
  );
  return data;
}

export async function saveButtonCycle(
  steps: CycleStep[],
): Promise<ButtonCycleSaveResult> {
  const { data } = await api.put<ButtonCycleSaveResult>(
    "/api/settings/button-cycle",
    { steps },
    { timeout: 20_000 },
  );
  return data;
}
