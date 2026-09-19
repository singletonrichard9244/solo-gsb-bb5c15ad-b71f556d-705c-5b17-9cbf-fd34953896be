# GS1 条码元素串 / Digital Link 解析 API

基于 Python + FastAPI 的 GS1 解析服务。`/parse` 接口同时接受两种输入：

1. **元素串（Element String）**：扫码枪原始输出（含 ASCII 29 / FNC1 分隔符）；
2. **GS1 Digital Link URI**：以 `http://` 或 `https://` 开头的链接，AI/值对位于路径
   （`/AI/值/AI/值`）和查询参数（`?AI=值&AI=值`）中。

两种输入都返回保持原顺序的结构化元素，并完成同一套字符集、长度、GTIN Mod-10
校验位与真实日期校验。重量 AI（310n/320n）在两种输入下行为完全一致，响应除
原始 `value` 外还会返回 `decimal_value`（按隐含小数位解释后的数值）与 `unit`。

## 一键启动

```bash
docker compose up --build
```

启动后访问：

- API 地址：http://localhost:8080
- 交互式文档（Swagger UI）：http://localhost:8080/docs
- 健康检查：http://localhost:8080/health

## 支持的 AI

| AI   | 含义        | 长度            | 字符集                          |
| ---- | ----------- | --------------- | ------------------------------- |
| 01   | GTIN        | 固定 14 位数字  | 0-9，含 Mod-10 校验位验证       |
| 17   | 效期 YYMMDD | 固定 6 位数字   | 0-9，校验真实日期（含闰年）     |
| 10   | 批号        | 可变 1-20 字符  | GS1 AI 字符集 82                |
| 21   | 序列号      | 可变 1-20 字符  | GS1 AI 字符集 82                |
| 310n | 净重（千克 kg） | AI 4 位 + 固定 6 位数字 | 0-9；`n` 为 0-9 的隐含小数位数 |
| 320n | 净重（磅 lb）   | AI 4 位 + 固定 6 位数字 | 0-9；`n` 为 0-9 的隐含小数位数 |

重量 AI 说明：

- 标识符共 4 位：前缀 `310`（千克）或 `320`（磅）+ 1 位小数位指示符 `n`
  （0-9），其后**固定 6 位数字**，按长度自动定界（与 01/17 一样无需分隔符）。
- 解析器按**最长可匹配**识别四位标识符：`310n`/`320n` 优先于两位 AI；
  例如 `3103...` 识别为 AI `3103`，而 `3110...` 仍按两位 AI `31` 处理
  （`31` 不受支持，返回 `UNSUPPORTED_AI`）。
- 值按整数除以 10^n 解释：`3103 002513` → `decimal_value = 2.513`，
  `unit = "kg"`；`3202 002513` → `25.13 lb`。`n = 0` 时为整数；
  `n > 6` 时高位补隐含的零，如 `3109 000001` → `0.000000001 kg`。
- 响应中的 `value` 保留**原始 6 位数字**（不插小数点），仅重量元素额外携带
  `decimal_value`（JSON number）与 `unit`（`"kg"` / `"lb"`）两个字段。

解析规则：

- 固定长度字段（01、17、310n、320n）按长度自动定界，无需分隔符。
- 可变长度字段（10、21）除位于串末尾外，必须以分隔符 ASCII 29（GS，扫码枪 FNC1）
  结束；JSON 中以 `\u001d` 表示。
- AI 17 的日字段为 `00` 时按 GS1 规范表示当月最后一天；年份按 20YY 解释。
- `data` 以 `http://` 或 `https://` 开头时按 GS1 Digital Link URI 解析，否则按
  元素串解析。

### Digital Link URI 规则

- 路径段必须成对出现：`/AI/值/AI/值...`；查询参数按 `AI=值&AI=值` 解析（参数没有
  等号时值为空，会触发 `FIELD_EMPTY`）。
- 值先做百分号 UTF-8 解码（`%XX`），再执行与元素串完全相同的字符集、长度、校验位与
  日期校验；`offset` 始终指向**原始 URI** 中的字节位置（解码后字符报错时定位到它的
  首个编码字节，即 `%` 所在字节）。
- 未知 AI、路径内重复 AI、查询参数内重复 AI，以及同一 AI 同时出现在路径和查询参数
  中，均返回 `422`。
- 响应中路径提取到的元素按路径顺序在前，查询参数提取到的元素按参数顺序在后。
- 片段（`#...`）不属于 Digital Link 数据，解析时忽略。

## 使用示例

### 元素串

请求（注意批号与序列号之间的 `\u001d` 分隔符）：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "01095011015300031725013110ABC123\u001d21SN0001"}'
```

响应 `200 OK`，元素按原始顺序返回，`offset` 为 AI 在输入中的字节偏移：

```json
{
  "elements": [
    { "ai": "01", "name": "GTIN",     "value": "09501101530003", "offset": 0  },
    { "ai": "17", "name": "EXPIRY",   "value": "250131",         "offset": 16 },
    { "ai": "10", "name": "BATCH/LOT","value": "ABC123",         "offset": 24 },
    { "ai": "21", "name": "SERIAL",   "value": "SN0001",         "offset": 33 }
  ]
}
```

### GS1 Digital Link URI

路径和查询参数混用的请求（路径中的元素在前，查询参数中的元素在后）：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "https://id.gs1.org/01/09501101530003/17/250131?10=ABC123&21=SN0001"}'
```

响应与元素串完全一致，`offset` 是 AI 在**原 URI** 中的字节偏移：

```json
{
  "elements": [
    { "ai": "01", "name": "GTIN",      "value": "09501101530003", "offset": 19 },
    { "ai": "17", "name": "EXPIRY",    "value": "250131",         "offset": 37 },
    { "ai": "10", "name": "BATCH/LOT", "value": "ABC123",         "offset": 47 },
    { "ai": "21", "name": "SERIAL",    "value": "SN0001",         "offset": 57 }
  ]
}
```

值可使用百分号编码，按 UTF-8 解码后再做同一套校验。下面的批号解码后为
`Lot A/100`（百分号编码指向原 URI 字节位置，示例中 `/` 编码为 `%2F`；
注意空格不属于字符集 82，仅作编码演示）：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "https://id.gs1.org/10/Lot%20A%2F100"}'
```

该请求会返回 `422 INVALID_CHARACTER`，`offset` 指向编码空格的 `%20` 首字节。
仅含合法字符的示例：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "https://id.gs1.org/10/Lot%2FA%2F100"}'
```

解码后批号为 `Lot/A/100`，返回 `200`。

路径段必须成对，例如 `.../01/09501101530003/17` 会返回 `422`
（`UNPAIRED_PATH`）；同一 AI 不能重复，也不能同时出现在路径和查询参数中
（`DUPLICATE_AI`）。

### 重量 AI（310n / 320n）

元素串与 Digital Link 两种输入返回完全相同的重量字段。元素串示例（`n=3`，
`002513` 表示 2.513 kg）：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "01095011015300033103002513"}'
```

Digital Link 示例（路径中的千克重量，查询参数中的磅重量）：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "https://id.gs1.org/01/09501101530003/3103/002513?3202=002513"}'
```

两种请求的重量元素结构一致，`value` 为原始 6 位数字，`decimal_value` 为按
`n` 位隐含小数点解释后的数值，`unit` 为单位：

```json
{
  "ai": "3103",
  "name": "NET WEIGHT (KG)",
  "value": "002513",
  "decimal_value": 2.513,
  "unit": "kg",
  "offset": 16
}
```

```json
{
  "ai": "3202",
  "name": "NET WEIGHT (LB)",
  "value": "002513",
  "decimal_value": 25.13,
  "unit": "lb",
  "offset": 49
}
```

非重量元素（01/10/17/21）不会出现 `decimal_value` 与 `unit` 字段。

## 错误响应

解析或校验失败返回 `422 Unprocessable Entity`，`detail` 中包含错误码、
字节偏移（`offset`，UTF-8 字节序）与预期规则（`expected`）：

```bash
curl -X POST http://localhost:8080/parse \
  -H "Content-Type: application/json" \
  -d '{"data": "0109501101530004"}'
```

```json
{
  "detail": {
    "error": "INVALID_CHECK_DIGIT",
    "message": "GTIN check digit mismatch: expected 3, found 4",
    "offset": 15,
    "expected": "Mod-10 check digit 3",
    "found": "4"
  }
}
```

错误码一览：

| 错误码                 | 说明                                                          |
| ---------------------- | ------------------------------------------------------------- |
| `EMPTY_INPUT`          | 输入为空（元素串为空，或 URI 路径/查询中没有任何 AI/值对）    |
| `UNEXPECTED_SEPARATOR` | 在应出现 AI 的位置遇到分隔符（仅元素串）                      |
| `INVALID_AI`           | 不是合法的数字 AI（元素串为非 2 位数字；URI 为非数字段/键）   |
| `UNSUPPORTED_AI`       | 不支持的 AI（仅支持 01/10/17/21 及 310n/320n）                |
| `INVALID_PRECISION`    | 310/320 前缀后缺少或给出非数字的精度位 `n`（须为 0-9）        |
| `FIELD_TOO_SHORT`      | 固定长度字段字符不足（含重量字段不足 6 位数字）               |
| `FIELD_EMPTY`          | 可变长度字段为空                                              |
| `FIELD_TOO_LONG`       | 可变长度字段超过 20 字符（或固定长度字段超长，仅 URI）        |
| `INVALID_CHARACTER`    | 字符不符合该字段字符集                                        |
| `INVALID_CHECK_DIGIT`  | GTIN Mod-10 校验位不匹配                                      |
| `INVALID_DATE`         | 效期不是真实日期                                              |
| `UNPAIRED_PATH`        | Digital Link URI 路径段不是成对的 AI/值（仅 URI）             |
| `DUPLICATE_AI`         | AI 重复出现，或同时出现在路径与查询参数中（仅 URI）           |
| `INVALID_PERCENT_ENCODING` | `%XX` 不是合法十六进制转义或不是合法 UTF-8（仅 URI）      |

## 本地开发

```bash
pip install -r requirements-dev.txt
pytest                 # 运行测试
uvicorn app.main:app --reload --port 8080
```

## 项目结构

```
app/
  main.py   # FastAPI 应用与请求/响应模型
  gs1.py    # GS1 元素串 / Digital Link URI 解析与校验核心
tests/
  test_parse.py
Dockerfile
docker-compose.yml
requirements.txt      # 运行依赖
requirements-dev.txt  # 测试依赖
```
