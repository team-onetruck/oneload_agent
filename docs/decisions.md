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
| D-16 | 대화 슬롯 저장 | Agent 는 상태 없음. `AgentResponse.slots` 를 Backend 가 저장 → 다음 `SessionContext.slots` 로 전달 | 합의 필요 | R4 | — |
| D-17 | 승차권 되묻기 버튼 | 명세 8.5 는 버튼에 `selected_poi_id` 를 넣지만, **열차 종류 버튼은 poi_id 없이 텍스트로** 답하게 함 → 구매 방법 답변 + 매표소 안내를 함께 줄 수 있음 | 잠정 | R2 + R4 · R5 | 화면에서 버튼 처리가 어려우면 |
| D-18 | 필수 슬롯 범위 | 승차권 주제는 `train_type`(직통 · 일반)만 필수. 행선지 · 결제 수단은 말했을 때만 검색어에 반영 | 잠정 | R2 + R1 · R3 | 시연 시나리오 확정 시 |
| D-19 | `search_pois` 카테고리 필터 | `filters={"category": "ticket_office"}` (되묻기 상한 초과 시 매표소 목록) | 합의 필요 | R4 | — |
| D-20 | 승차권 사실 정보 | 요금 · 운행 시간 · 버튼 이름은 R3 공식 자료에서만. 프롬프트 · 코드에 쓰지 않음 | 확정 | R2 · R3 | — |
| D-21 | 남은 좌석 · 가장 빠른 열차 | **남은 좌석은 다루지 않음** (공개 잔여석 API 확인 못 함). 시각표 기준 **도착 시각**으로 가장 빨리 도착하는 열차만 안내 | 확정 (2026-10-22 사용자) | 팀 | 공식 잔여석 API 확보 시 |
| D-22 | 시각표 도구 | Agent 도구 `get_departures(origin, destination, now, limit) -> list[Departure]` 추가. 장애 · 데이터 없음이면 시각 문장 없이 본 답변 유지 | 합의 필요 (명세 10장 "도구 2개" 변경) | R4 + R2 · R3 | — |
| D-23 | 행선지 기본값 | 터미널을 말하지 않으면 인천공항1터미널 기준, 문장에 "기준 터미널" 항상 표시. 2터미널을 말하면 2터미널 | 잠정 | R2 + R1 | 시연 시나리오 확정 시 |

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

## 2026-10-22 오후 · 백엔드(oneload-backend) 규격 채택

| No | 질문 | 결정 | 상태 | 결정자 | 영향 |
|---|---|---|---|---|---|
| D-24 | R2 규격 기준 | **oneload-backend `app/ai/contracts.py` 를 그대로 따른다.** 사본을 `hangil_r2/backend/` 에 두고 `scripts/check_backend_compat.py` 로 일치 확인. 백엔드 저장소에는 올리지 않음 | 확정 (사용자) | 사용자 | D-03 · D-04 · D-05 · D-16 · D-17 은 백엔드 값으로 대체 |
| D-25 | 도착 시각 비교 (D-21 · D-22) | 기본 **꺼짐**. 백엔드 RuleAgent 의 "확인된 요금 · 소요시간 없으면 비교 안 함" 과 맞춤. 공식 시각표 도구가 생기면 `get_departures` 를 넘겨 켬 | 확정 | R2 | — |
| D-26 | 위치 순위 방식 | 백엔드 `match_location` 유지. R2 `rank_location` 은 같은 입출력의 **개선 제안** (드문 문구 가중 · 오타 허용 · 동점 안내) | 제안 | R4 | 바꾸면 R6 사진 평가로 비교 |
| D-27 | 대화 상태 | Agent 는 상태 없음. 되묻기 맥락은 백엔드가 저장하는 `slots` + `recent_messages`(10개)로 이어 감. 내 이전 `pending_intent` 방식은 폐기 | 확정 | R2 | — |
| D-28 | LLM 장애 | `fallback=RuleAgent()` 로 백엔드 규칙 응답. fallback 없으면 `cannot_answer` | 제안 | R2 + R4 | — |
| D-29 | R3 검색 필터 | `context["slots"]["train_type"]` 로 다른 열차 전용 문서 제외, 요금 문서는 `scope="fare"` | 합의 필요 | R3 | — |

**대체된 항목**: D-02(표지판 형식 → 백엔드 `landmarks.sign_texts`), D-03 · D-04 · D-05(status · intent · next_action → 백엔드 값), D-07 · D-08 · D-10(위치 후보 · hint → 백엔드 `match_location` · `retake_hint`), D-16 · D-17(slots 저장 · 버튼 → 백엔드 `Slots`), D-18 · D-23(행선지 · 결제 슬롯 → 백엔드 Slots 에 없음, 사용 안 함).
