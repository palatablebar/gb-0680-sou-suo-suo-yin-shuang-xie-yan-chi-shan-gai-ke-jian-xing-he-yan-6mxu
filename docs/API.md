# 接口

模块GET使用仓内样例，模块POST可传csv或rows与context，直接计算当前规则。/api/runs使用完整流水线并原子保存。每个阶段返回rows、quarantine、metrics；后续阶段还返回累计隔离。/api/upload接收multipart文件file。/api/runs/<id>/archive只导出一份带清单与证据的CSV。

## 判定与补正

- `POST /api/runs/<id>/review`：首次签发。携带version与decision，成功后批次current_seq=1，原判定与证据哈希固化；已签发批次再调返回409 REVIEW_SUPERSEDED，更正只能走补正。
- `POST /api/runs/<id>/corrections`：追加补正判定。参数version（补正基于的批次版本）、decision、reason（必填）、idempotency_key（补正单号，必填）。补正追加为seq+1的新判定，旧判定只写superseded_by去向，不改写；成功后新判定成为唯一正式判定（runs.current_seq指向它）。
  - 同一idempotency_key重试且内容一致：幂等返回，不新增签发版、不动版本号；同号不同内容返回422 IDEMPOTENCY_CONFLICT。
  - version不等于当前版本：409 STALE_CORRECTION，迟到旧补正不能覆盖新版。
  - 未签发先补正：422 NO_BASE_JUDGMENT；缺理由/单号：422 BAD_REASON / BAD_IDEMPOTENCY。
- `GET /api/runs/<id>/judgments`：判定链，含每版decision、reason、basis_version、evidence_sha256、superseded_by、issued_at及current_seq。
- `GET /api/runs/<id>/backfill` 与 `GET /api/backfills/<id>`：回补视图，每行并排original_status（原对账结论，只读）、backfill_status（当前判定作用后的去向）、judgment_seq与judgment_id；退回判定下行状态为“挂起待重核”。
- `GET /api/runs/<id>/archive`：导出CSV分四类kind——accepted/quarantine为原签发证据（补正前后逐字节不变），backfill为回补清单（原结论+现行判定），judgment为判定台账（原签发版、当前版及去向）。

