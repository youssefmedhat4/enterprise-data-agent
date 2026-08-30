export type ModelProfile = "qwen" | "gemini";

export const MODEL_PROFILES: ReadonlyArray<{
  value: ModelProfile;
  label: string;
}> = [
  { value: "qwen", label: "Qwen 3.6 27B" },
  { value: "gemini", label: "Gemini 2.5 Flash" },
];

export const DEFAULT_MODEL_PROFILE: ModelProfile = "qwen";

export function modelDisplayName(profile: ModelProfile): string {
  return MODEL_PROFILES.find((candidate) => candidate.value === profile)?.label ?? profile;
}
