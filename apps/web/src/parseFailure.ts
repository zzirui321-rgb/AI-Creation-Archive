export function parseFailure(job: { state: string; error_code?: string | null; error_detail?: { stage?: string } | null }) {
  const stage = job.error_detail?.stage;
  const reasons: Record<string, string> = {
    output_truncated: 'AI 输出达到长度上限，返回的 JSON 不完整',
    output_unfinished: 'AI 未正常完成输出',
    proposal_json: 'AI 返回的内容不是完整有效的 JSON',
    proposal_schema: 'AI 返回的模块字段不符合 Graph 格式',
    graph_validation: 'AI 返回的来源引用、归属或关系未通过校验',
    response_envelope: '模型服务返回格式异常',
    response_usage: '模型服务返回的用量格式异常',
  };
  return `解析未成功：${stage && reasons[stage] ? reasons[stage] : job.error_code === 'invalid_semantic_graph' ? 'AI 结果未通过 Graph 校验（旧任务没有保存详细原因）' : job.error_code ?? job.state}。原档案已保留；不会自动重试模型请求。`;
}
