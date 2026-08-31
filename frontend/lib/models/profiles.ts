/**
 * Model profiles the UI may offer.
 *
 * The browser sends only these bounded profile names; the backend owns every
 * provider, model string, and credential. `qwen` remains a valid backend
 * profile for existing deployments but is deliberately not offered here.
 */
export type ModelProfile = "gemini_pro" | "gemini";

export const MODEL_PROFILES: ReadonlyArray<{
  value: ModelProfile;
  label: string;
}> = [
  { value: "gemini_pro", label: "Gemini 3.1 Pro Preview" },
  { value: "gemini", label: "Gemini 2.5 Flash" },
];

export const DEFAULT_MODEL_PROFILE: ModelProfile = "gemini_pro";

export function isModelProfile(value: unknown): value is ModelProfile {
  return MODEL_PROFILES.some((candidate) => candidate.value === value);
}

export function modelDisplayName(profile: ModelProfile): string {
  return MODEL_PROFILES.find((candidate) => candidate.value === profile)?.label ?? profile;
}
