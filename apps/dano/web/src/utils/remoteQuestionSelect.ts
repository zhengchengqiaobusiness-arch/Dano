export type RemoteQuestionSelectOption = {
  key: string;
  label: string;
};

export type RemoteQuestionSelectStatus = "loading" | "error" | "empty" | "ready";

export function remoteQuestionSelectStatus({
  loading,
  error,
  optionCount,
}: {
  loading: boolean;
  error: boolean;
  optionCount: number;
}): RemoteQuestionSelectStatus {
  if (loading && optionCount === 0) return "loading";
  if (error) return "error";
  if (optionCount === 0) return "empty";
  return "ready";
}

export function selectedRemoteQuestionOption(
  value: string,
  options: RemoteQuestionSelectOption[],
): RemoteQuestionSelectOption | undefined {
  return options.find(option => option.key === value);
}
