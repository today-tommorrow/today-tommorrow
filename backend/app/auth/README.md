# 인증 저장 기반

`AuthStore`는 `today_app`의 사용자, 세션, OAuth 로그인 state만 저장한다. Google 요청·토큰 검증은 `google.py`, API·트랜잭션·쿠키는 `routes.py`가 맡는다. 로컬 설정과 재현 절차는 [Google 로그인 가이드](../../../docs/google-login.md)를 따른다.

- `save_google_identity`를 호출하기 전에 호출자가 Google 서명, `iss`, `aud`, `exp`, `nonce`를 검증해야 한다. 사용자 식별자는 Google `sub`이며 같은 이메일로 다른 `sub`를 합치지 않는다.
- 생성·조회·소비 시각은 호출자가 timezone-aware `datetime`으로 전달한다. 유효 범위는 `created_at <= now < expires_at`이다. 생성 시 만료는 현재 시각보다 늦어야 한다.
- `AuthStore(db, signing_key=...)`에 최소 32바이트의 비밀 키를 명시한다. 기본 키는 없다. 동일 키로 세션별 CSRF 값을 재구성하며 다른 키에서는 기존 세션을 거절한다. 키 변경 시 기존 세션의 재로그인이 필요하다.
- 세션 token, OAuth state, browser binding, nonce는 각각 256비트 난수다. DB에는 SHA-256 해시만 저장한다. CSRF는 비밀 키와 세션 token의 HMAC-SHA256으로 파생하고 그 해시를 저장한다. 반환 객체의 `repr`은 원시 보안값을 숨기지만 `asdict` 등 별도 직렬화까지 보호하지 않는다. 키와 원시 token·state·binding·nonce·CSRF는 로그에 남기지 않는다.
- `consume_oauth_state`는 browser binding, 유효 시각, 미소비 상태를 한 번의 `UPDATE ... RETURNING`으로 확인한다. 성공한 레코드의 `nonce_hash`는 `nonce_matches(record, verified_nonce)`로 비교한다. 잘못된 state나 binding은 소비하지 않는다.

저장소는 `commit`이나 `rollback`을 호출하지 않는다. 생성된 UUID를 얻기 위한 `flush`와 SQL 실행만 수행한다. 호출자가 세션 수명을 관리하고 성공 시 `commit`, 실패 시 `rollback`을 수행해야 한다. `Session`은 요청별로 생성하며 여러 스레드가 공유하지 않는다.

OAuth callback은 **state 소비에 성공하면 해당 트랜잭션을 별도로 commit한 뒤 외부 Google 통신을 시작해야 한다.** 외부 요청 실패 후 rollback으로 state를 복원하면 동일 callback을 다시 사용할 수 있다. 이후 Google 검증 및 사용자·세션 생성은 별도 트랜잭션으로 처리한다. OAuth 로그인 시작 시 발급한 state도 응답 전에 commit해야 한다.

잘못된 생성 입력은 민감한 원문을 포함하지 않은 `ValueError` 코드로 거절한다. 없거나 잘못된 보안값, 만료·미래·취소된 세션, 이미 소비한 state는 `None` 또는 `False`를 반환한다. 세션 취소는 첫 호출에서만 `True`이며 같은 token을 반복 취소하면 `False`다.
