export interface MutationAuthorizedRequest {
  allowMutations: boolean;
}

export function withCurrentMutationAuthorization<
  Request extends MutationAuthorizedRequest,
>(request: Request, allowMutations: boolean): Request {
  return { ...request, allowMutations };
}
