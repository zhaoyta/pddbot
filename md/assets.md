# assets/ 目录说明

放置自动客服需要的本地静态资源。

## 必需文件

| 文件 | 用途 | 状态 |
|---|---|---|
| `card_code_guide.png` | S2 阶段发给"已下单未核销"客户的"如何获取核销码"教程图 | **请你拷贝过来** |

## 文档用可选资源（`@assets/`）

对外说明见 [`community.md`](community.md)。

| 文件 | 用途 |
|---|---|
| `@assets/gerenerweima.JPG` | 微信加好友二维码（加群前先添加，备注 **PDDBOT**） |
| `@assets/dashang.JPG` | 微信支付自愿打赏二维码 |
| `@assets/guide.gif` | README 使用指导动图（首页启动机器人） |

## 命名约定

- 图片：`{stage}_{purpose}.png`，比如 `s2_card_code_guide.png`、`s4_redeem_success.png`
- 推荐尺寸：宽度 720~1080px，文件 < 500KB

## 引用方式

代码里通过 ``core.config.CARD_CODE_GUIDE_IMAGE`` 路径引用：

```python
from core.config import CARD_CODE_GUIDE_IMAGE
send_image(uid, str(CARD_CODE_GUIDE_IMAGE))
```

## 后续规划

如果以后需要按商品发不同教程图（例如不同品类核销方式不一样），改成目录结构：

```
assets/
├── guide/
│   ├── default.png
│   ├── sku_1876675563671.png
│   └── ...
└── ...
```

`tools.messaging.send_card_code_guide(sku_id)` 自动按 sku_id 找对应图片，没有就用 default。
