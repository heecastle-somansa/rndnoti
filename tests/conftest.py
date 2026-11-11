"""모든 테스트 공통 설정."""
import pytest


@pytest.fixture(autouse=True)
def _no_github_side_effects(monkeypatch):
    """테스트가 GitHub Actions의 실제 실행 요약(Summary)·로그 마스킹에 흔적을 남기지 않게 한다.

    (GitHub에서 테스트를 돌릴 때, 일부러 만든 '설정 파일 오류' 문구가 실행 요약에 찍히던 문제 방지)
    """
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
