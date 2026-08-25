SYSTEM_NAME = "FoodDeliverySupportAI"
POLICY_VERSION = "poc-0.1"
CLASSIFIER_VERSION = "poc-0.1"
RETRIEVAL_VERSION = "poc-0.1"

TOPIC_LABELS = ["order_delivery", "payment", "after_sales", "account", "other"]

CONFIDENCE_THRESHOLD = 0.7

RISKY_KEYWORDS = [
    "扣款",
    "重复扣款",
    "退款",
    "退钱",
    "支付失败",
    "银行卡",
    "盗号",
    "被盗",
    "异常登录",
    "起诉",
    "报警",
]

TOPIC_KEYWORDS = {
    "order_delivery": ["配送", "骑手", "超时", "联系不上", "取餐", "漏送", "错送", "损坏"],
    "payment": ["支付", "扣款", "扣了", "扣钱", "两次", "银行卡", "余额", "失败", "重复", "账单", "退款"],
    "after_sales": ["售后", "退款", "退差价", "赔付", "差评", "申诉", "投诉"],
    "account": ["账号", "登录", "密码", "手机号", "修改", "找回", "申诉"],
}
