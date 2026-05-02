# Watermelon Personal Site

재훈 님의 개인 사이트를 **Companion(Banana)와 분리된 독립 서비스**로 운영하기 위한 기본 프로젝트입니다.  
핵심은 "작은 웹앱 + PostgreSQL DB + Docker" 3가지를 한 세트로 묶어두는 것입니다.

## 1. 이 프로젝트가 하는 일

- 개인 소개/프로젝트 목록/연락처를 보여주는 웹 페이지를 제공합니다.
- 데이터는 PostgreSQL(데이터베이스)에 저장합니다.
- Docker로 통째로 묶어 운영서버에서 같은 방식으로 실행할 수 있습니다.

비유로 보면:
- 웹앱 = 가게 진열대
- DB = 창고
- Docker = 가게를 통째로 포장한 컨테이너

## 2. 빠른 시작 (로컬 또는 서버 공통)

1. `.env` 만들기
```bash
cd /workspace/dev_codeserver_Personal
./scripts/setup_env.sh
```

2. `.env`에서 최소 2개 값 수정
- `POSTGRES_PASSWORD`
- `CONTACT_EMAIL`

3. 컨테이너 실행
```bash
docker compose up -d --build
```

4. 상태 확인
```bash
docker compose ps
docker inspect --format '{{.State.Health.Status}}' watermelon-personal-web
```

5. 접속 확인  
- `http://서버IP:985`

## 3. 운영서버 반영용 스크립트

아래 스크립트는 `docker compose up -d --build`와 상태 확인을 한 번에 실행합니다.

```bash
cd /workspace/dev_codeserver_Personal
./scripts/deploy_personal.sh
```

## 4. 폴더 역할

- `app/`: 실제 사이트 코드
- `docker-compose.yml`: 웹앱 + DB를 같이 올리는 실행 설계도
- `Dockerfile`: 웹앱 컨테이너 제작 레시피
- `.env.example`: 환경값 샘플
- `scripts/`: 반복 작업 자동화 스크립트

## 5. 중요한 원칙

- `Banana` 폴더(`/workspace/dev_codeserver_Banana`)는 **참고 전용**입니다.
- 이 프로젝트(`dev_codeserver_Personal`)에서만 수정/배포 작업을 진행합니다.
