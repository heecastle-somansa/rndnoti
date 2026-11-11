# R&D Noti

나라장터 입찰공고와 정부 R&D 과제 공고를 **매주 월요일 08:00**에 자동으로 모아, 구독자가 고른 **분류/키워드**에 맞는 공고만 메일로 보내 줍니다.

- 서버 없이 GitHub Actions로 돌아갑니다.
- 구독자마다 한 통씩 따로 보내므로 서로의 주소가 보이지 않습니다.
- 출처·키워드·구독자는 설정 파일과 GitHub Secrets만 고치면 바뀝니다. 코드는 건드리지 않습니다.

화면별 클릭 순서와 자세한 설명은 **[상세 가이드(GUIDE.md)](GUIDE.md)** 에 있습니다.

## 처음 시작하기

**1. 비밀값 준비** ([상세](GUIDE.md#1-처음-한-번만-하는-설정))

- [공공데이터포털](https://www.data.go.kr)에서 `조달청_나라장터 입찰공고정보서비스`와 `조달청_나라장터 사전규격정보서비스`를 활용신청하고 **일반 인증키(Decoding)** 를 복사합니다.
- 발송용 Gmail에서 2단계 인증을 켜고 [앱 비밀번호](https://myaccount.google.com/apppasswords) 16자리를 만듭니다.

**2. GitHub Secrets 등록** — 저장소 **[Settings] → [Secrets and variables] → [Actions] → [New repository secret]**

| Name | 값 |
| --- | --- |
| `DATA_GO_KR_KEY` | 공공데이터포털 일반 인증키(Decoding) |
| `SMTP_USER` | 발송용 Gmail 주소 |
| `SMTP_PASSWORD` | Gmail 앱 비밀번호 16자리 (띄어쓰기 없이) |
| `ADMIN_EMAIL` | 오류·점검 결과를 받을 운영자 메일 |
| `SUBSCRIBERS_JSON` | 구독자 목록 (아래 형식) |

```json
[
  {"name": "본인", "email": "me@example.com", "active": true,
   "categories": ["망분리·VDI", "보안 일반", "바우처·인프라 지원"]},
  {"name": "지인", "email": "friend@example.com", "active": true,
   "categories": ["생체인증·출입보안"],
   "source_categories": ["입찰"]}
]
```

`categories`에는 [config/keywords.yaml](config/keywords.yaml)의 분류 이름을 글자 그대로 적습니다. `source_categories`는 선택 항목으로, 받을 공고 카테고리(`R&D`, `입찰`, `바우처`)만 고를 때 적습니다. Secrets는 저장 후 다시 볼 수 없으니 **구독자 목록 원본은 PC에 따로 보관**하세요.

**3. 시험 실행** — 저장소 **[Actions] → R&D Noti weekly report → [Run workflow]** 에서 모드를 골라 아래 순서로 실행합니다.

| 순서 | 모드 | 하는 일 |
| --- | --- | --- |
| ① | `check_sources` | 출처별 수집 성공/실패를 운영자 메일로 보냄 |
| ② | `dry-run` | 메일을 보내지 않고 미리보기 파일만 만듦. 실행 화면 아래 **Artifacts**에서 내려받아 확인 |
| ③ | `normal` | 구독자에게 실제 발송 |

**4. 끝.** 이후에는 매주 월요일 08:00(한국 시간)에 자동으로 발송됩니다.

## 바꾸고 싶을 때

GitHub 웹 화면에서 파일을 열고 **연필 아이콘(✏️)** → 수정 → **[Commit changes]** 를 누르면 다음 실행부터 반영됩니다.

| 바꾸려는 것 | 고칠 곳 |
| --- | --- |
| 분류·키워드·제외어 | [config/keywords.yaml](config/keywords.yaml) |
| 출처 켜기/끄기, 주소 변경, 카테고리 | [config/sources.yaml](config/sources.yaml) (`enabled`, `url`, `category`) |
| 메일 섹션(발주처 그룹) 이름·순서 | [config/groups.yaml](config/groups.yaml) |
| 구독자 추가·수정·일시중지 | Secrets `SUBSCRIBERS_JSON` (전체를 다시 붙여넣기) |
| 발신 계정 | Secrets `SMTP_USER`, `SMTP_PASSWORD` |
| 발송 요일·시간 | [.github/workflows/weekly-report.yml](.github/workflows/weekly-report.yml)의 `cron` 한 줄 |

- 설정 파일을 저장하면 1분 안에 자동 검사가 돕니다. 커밋 옆에 **초록 체크(✓)** 면 정상, **빨간 X(✗)** 면 눌러서 한국어 오류 문구를 확인하고 고칩니다.
- 분류 이름을 바꾸거나 지웠다면 `SUBSCRIBERS_JSON`의 `categories`도 맞춰 주세요.
- **새 게시판을 출처로 추가**하려면 화면에서 공고 위치를 찾는 규칙이 필요합니다. Claude Code에 "이 주소를 출처로 추가해줘"라고 요청하세요.

## 문제가 생겼을 때

| 증상 | 확인할 것 |
| --- | --- |
| 메일이 안 옴 | [Actions] 탭에서 빨간 ✕ 실행을 눌러 `Run` 단계 로그 확인. 운영자 메일로 온 오류 메일 확인 |
| 메일 서버 로그인 실패 | `SMTP_PASSWORD`가 일반 비밀번호가 아닌 **앱 비밀번호**인지 확인 |
| 나라장터만 실패 | 인증키 활용신청 2건이 모두 승인됐는지, **Decoding** 키인지 확인 |
| `출처 확인 필요` 메일이 옴 | 기관 사이트 주소가 바뀌었거나 접속이 안 되는 경우. 메일에 적힌 새 주소로 `config/sources.yaml`의 `url`을 바꿈. 그래도 실패하면 Claude Code에 수정 요청 ([상세](GUIDE.md#출처-주소가-바뀌거나-수집이-실패할-때-자동-추적)) |
| 같은 공고가 또 옴 | **정정** 또는 **리마인드**(마감 7일 이내) 배지가 붙은 정상 동작 |
| 받는 사람 스팸함에 들어감 | 받는 분이 메일에서 **[정크 아님]** 을 한 번 눌러 주면 됨 |

더 많은 경우는 [상세 가이드 6장](GUIDE.md#6-문제가-생겼을-때)을 보세요.

## 알아둘 점

- 메일은 **부처별 R&D / 나라장터·기관 입찰 / 바우처·지원사업** 세 카테고리로 나뉩니다. 구독자마다 받을 카테고리를 고를 수 있습니다. ([상세](GUIDE.md#공고-카테고리-rd--입찰--바우처))
- 현재 26개 출처 중 19곳을 수집합니다. robots.txt가 금지하거나 접속이 거부되는 7곳은 꺼 두었습니다. ([출처 현황](GUIDE.md#4-수집-출처-현황))
- 실행 기록과 미리보기 파일은 저장소에 쌓이지 않고 GitHub가 자동으로 지웁니다. 저장소에는 `state/` 파일 2개만 매주 덮어씁니다.
- 인증키·메일 계정·구독자 주소는 Secrets에만 두며, 파일이나 로그에 남지 않습니다.
