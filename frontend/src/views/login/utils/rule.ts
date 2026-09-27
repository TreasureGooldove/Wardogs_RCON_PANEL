import { reactive } from "vue";
import type { FormRules } from "element-plus";

/** 登录校验 */
const loginRules = reactive<FormRules>({
  username: [
    { required: true, message: "请输入账号", trigger: "blur" },
    { min: 3, max: 64, message: "账号长度应为 3 到 64 个字符", trigger: "blur" }
  ],
  password: [
    { required: true, message: "请输入密码", trigger: "blur" }
  ]
});

export { loginRules };
