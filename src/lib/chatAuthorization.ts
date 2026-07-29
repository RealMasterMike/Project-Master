export interface ToolAuthorizedRequest {
  allowMutations: boolean;
  allowWebSearch: boolean;
}

export function withCurrentToolAuthorization<Request extends ToolAuthorizedRequest>(
  request: Request,
  authorization: Pick<
    ToolAuthorizedRequest,
    "allowMutations" | "allowWebSearch"
  >,
): Request {
  return { ...request, ...authorization };
}
