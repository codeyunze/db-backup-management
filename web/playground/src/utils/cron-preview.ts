/**
 * 标准 5 段 cron（分 时 日 月 周）解析，计算从当前时刻起的若干次预计执行时间。
 * 逻辑参考 db-backup-management/api/templates/index.html
 */

function parseCronField(field: string, min: number, max: number): Set<number> {
  const values = new Set<number>();
  if (field === '*') {
    for (let i = min; i <= max; i++) values.add(i);
    return values;
  }
  const parts = field.split(',');
  for (const part of parts) {
    if (!part) continue;
    const stepMatch = part.match(/^(\*|\d+)(?:\/(\d+))?$/);
    if (stepMatch) {
      const startRaw = stepMatch[1];
      const stepRaw = stepMatch[2];
      const start = startRaw === '*' ? min : Number.parseInt(startRaw, 10);
      const step = stepRaw ? Number.parseInt(stepRaw, 10) : null;
      if (!Number.isNaN(start) && start >= min && start <= max) {
        if (step && step > 0) {
          for (let i = start; i <= max; i += step) values.add(i);
        } else {
          values.add(start);
        }
      }
      continue;
    }
    const rangeMatch = part.match(/^(\d+)-(\d+)(?:\/(\d+))?$/);
    if (rangeMatch) {
      let start = Number.parseInt(rangeMatch[1]!, 10);
      let end = Number.parseInt(rangeMatch[2]!, 10);
      const step = rangeMatch[3] ? Number.parseInt(rangeMatch[3], 10) : 1;
      if (!Number.isNaN(start) && !Number.isNaN(end) && step > 0) {
        start = Math.max(min, start);
        end = Math.min(max, end);
        for (let i = start; i <= end; i += step) values.add(i);
      }
      continue;
    }
    const v = Number.parseInt(part, 10);
    if (!Number.isNaN(v) && v >= min && v <= max) values.add(v);
  }
  return values;
}

/**
 * 计算从「下一分钟」起的若干次匹配时间。
 */
export function computeNextCronRuns(
  expr: string,
  count: number,
): Date[] | null {
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [minF, hourF, domF, monthF, dowF] = parts;
  const minutes = parseCronField(minF!, 0, 59);
  const hours = parseCronField(hourF!, 0, 23);
  const doms = domF === '*' ? null : parseCronField(domF!, 1, 31);
  const months = monthF === '*' ? null : parseCronField(monthF!, 1, 12);
  const dows = dowF === '*' ? null : parseCronField(dowF!, 0, 6);

  if (minutes.size === 0 || hours.size === 0) return null;

  const results: Date[] = [];
  let dt = new Date();
  dt.setSeconds(0, 0);
  dt = new Date(dt.getTime() + 60 * 1000);
  let steps = 0;
  const MAX_STEPS = 2 * 365 * 24 * 60;

  while (results.length < count && steps < MAX_STEPS) {
    const minute = dt.getMinutes();
    const hour = dt.getHours();
    const dom = dt.getDate();
    const month = dt.getMonth() + 1;
    const dow = dt.getDay();

    const monthOk = !months || months.has(month);
    const domOk = !doms || doms.has(dom);
    const dowOk = !dows || dows.has(dow);

    if (minutes.has(minute) && hours.has(hour) && monthOk && domOk && dowOk) {
      results.push(new Date(dt));
    }

    dt = new Date(dt.getTime() + 60 * 1000);
    steps++;
  }

  return results;
}

export type CronValidateResult =
  | { message: string; valid: false }
  | { valid: true };

/**
 * Cron 合规校验：5 段标准表达式 + 能解析出至少一次预计执行时间。
 */
export function validateCronExpression(expr: string): CronValidateResult {
  const trimmed = expr.trim();
  if (!trimmed) {
    return { valid: false, message: '请输入 Cron 表达式' };
  }
  const parts = trimmed.split(/\s+/);
  if (parts.length !== 5) {
    return {
      valid: false,
      message:
        '须为 5 段标准 cron（分 时 日 月 周），用空格分隔，例如 0 * * * * 表示每小时整点',
    };
  }
  const runs = computeNextCronRuns(trimmed, 1);
  if (runs === null) {
    return {
      valid: false,
      message:
        'Cron 表达式不合法或无法解析，请检查各段取值（分 0-59，时 0-23，日 1-31，月 1-12，周 0-6）',
    };
  }
  if (runs.length === 0) {
    return {
      valid: false,
      message: '该表达式在可解析范围内无法匹配到执行时间，请修改后重试',
    };
  }
  return { valid: true };
}
