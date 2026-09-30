import { t } from "@/i18n";
import { computed } from "vue";
import type { FormRules } from "element-plus";

/** 登录校验 */
const loginRules = computed<FormRules>(() => ({
  username: [
    { required: true, message: t("请输入账号"), trigger: "blur" },
    {
      min: 3,
      max: 64,
      message: t("账号长度应为 3 到 64 个字符"),
      trigger: "blur"
    }
  ],
  password: [{ required: true, message: t("请输入密码"), trigger: "blur" }]
}));

export { loginRules };
