# 결정 기록 (R2)

미정 항목은 **잠정값으로 먼저 진행**하고, 코드에서는 상수 · `.env` 로 둬서 합의 후 바로 바꿀 수 있게 한다.
상태: 확정 / 잠정 (R2 결정, 팀 공유) / 합의 필요 (상대 역할 확인 대기)

| No | 질문 | 결정 · 잠정값 | 상태 | 결정자 | 다시 볼 조건 |
|---|---|---|---|---|---|
| D-01 | 위치 확인의 주 수단 | **사진 인식** (마커는 보조 · 미사용) | 확정 (2026-10-22 팀) | 팀 | 현장 평가 top-3 < 70% |
| D-02 | 노드 표지판 데이터 위치 · 형식 | landmarks 에 `reference_type: "sign"`(text, facing_deg) · `"feature"`(kind) 추가 | 합의 필요 | R4 + R2 | 답사에서 표지판 없는 노드가 많으면 |
| D-03 | `AgentResponse.status` 값 | 명세 2개 + `answered` · `needs_review` · `needs_location` · `denied` | 잠정 | R2 | R4 · R5 화면 분기와 충돌 시 |
| D-04 | `intent` 목록 | `navigate_to_poi` · `ask_procedure` · `ticket_purchase_and_navigation` · `report_lost` · `out_of_scope` | 잠정 | R2 | 평가셋 오분류 3건 이상 |
| D-05 | `next_action` 값 | `reply` · `locate` · `navigate` · `none` | 잠정 | R2 + R4 | — |
| D-06 | 추가 질문 상한 | 2회 (`MAX_CLARIFY`) → 이후 목록 직접 선택 | 잠정 | R2 | 시연에서 사용자가 막히면 |
| D-07 | 위치 후보 수 · 애매 기준 | 최대 3개, 1 · 2위 차 < 0.1 이면 hint (`AMBIGUITY_MARGIN`) | 잠정 | R2 + R6 | 현장 평가 후 |
| D-08 | 다중 촬영 결합 | `LocalizationContext.previous_candidates` 추가 (Backend 가 직전 후보 전달) | 합의 필요 | R4 | — |
| D-09 | 사용자 설명 단서 | `LocalizationContext.user_text` 추가 | 합의 필요 | R4 · R5 | — |
| D-10 | `LocationEstimate.hint` | 추가 (다음 촬영 안내 문장) | 합의 필요 | R4 · R5 | — |
| D-11 | POI id 로 조회 | `search_pois(map_id, "", {"poi_id": id, "verified": True})` | 합의 필요 | R4 | — |
| D-12 | R2 처리 시간 예산 | 15초 중 R2 몫: LLM 1회당 6초 상한(`LLM_TIMEOUT_S`), 재시도 1회 | 잠정 | R2 + R4 | 측정 p95 초과 시 |
| D-13 | `ar_pose_at_capture` 사용 | MVP 에서는 사용하지 않음 (방향은 표지판 기준) | 잠정 | R2 + R5 | 마커 정합을 다시 쓰게 되면 |
| D-14 | 다국어 | ko · en 고정 문구, ja · zh 는 en 대체 | 잠정 | R1 · R5 | 시연 언어 확정 시 |
| D-15 | LLM 모델 | 이미지 입력 지원 모델, `OPENAI_MODEL` 로 교체 | 합의 필요 | 팀 | 비용 · 속도 측정 후 |

## 결정 카드 양식

```
질문     : 무엇을 정하나
선택지   : 2~3개
기준     : 시연 성공에 필요한가 · 3일 안에 되는가 · 틀렸을 때 위험한가
잠정값   : 지금 쓰고 진행할 값 (코드에서는 설정값으로)
결정자   : 명세 1장 소유권 기준
기한     : 어느 CP 까지
다시 볼 조건 : 예) 평가셋 오분류 3건 이상
```
