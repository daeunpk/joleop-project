# GitHub Structure Guide

이 프로젝트는 루트에 모든 코드를 두지 않고 역할별 폴더로 나눕니다.

## Commit 대상

GitHub에 올릴 파일/폴더:

```txt
ai/
backend/
shared/
docs/
legacy/
.env.example
.gitignore
README.md
docker-compose.yml
requirements.txt
```

## Commit 제외 대상

GitHub에 올리지 않는 로컬 파일/폴더:

```txt
.env
.venv/
tools/
images/
audio/
tmp/
generated_images/
```

## 왜 루트 파일을 전부 ai/backend에 넣지 않나요?

`ai/`와 `backend/`는 실행 코드 폴더입니다. 하지만 아래 파일들은 프로젝트 전체를 설명하거나 실행 환경을 정의하기 때문에 루트에 두는 것이 표준입니다.

```txt
README.md           # 프로젝트 설명
requirements.txt    # Python 의존성
docker-compose.yml  # PostgreSQL 실행
.env.example        # 환경변수 예시
.gitignore          # Git 제외 규칙
```

`shared/`는 AI와 백엔드가 같이 쓰는 공통 코드입니다.

```txt
shared/settings.py  # 환경변수/모델 설정
shared/models.py    # 공통 데이터 모델
```

이 구조가 GitHub에서 가장 읽기 쉽고, 다른 사람이 clone한 뒤 실행하기도 쉽습니다.
