/** 首期不提供按钮级操作权限；业务访问由服务端会话验证。 */
export const hasPerms = (_value: string | string[]): boolean => false;
