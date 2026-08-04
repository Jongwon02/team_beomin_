# team_beomin_
team_Repo
1. 체크리스트 초기화 안 됨 수정 완료
    - 부모요소(캘린더) 삭제 시 자식요소(체크리스트)가 같이 삭제 안 되는 오류 발생
    - 변경 내용: 계획을 지울 때 mf.crop(작물명)을 접두사로 삼아, beomin_checklist_status/beomin_prep_suggestions에서 그 작물로 시작하는 키를 전부 지우고(로컬 + setState), 로그인 상태면 Supabase의 checklist_status/prep_status 테이블에서도 item_key/prep_key가 그 접두사로 시작하는 행을 .like()로 함께 삭제하도록 했습니다.
2. 작물을 얼마나 키웠고, 현재 어떤 상태인지 팝업으로 받기
    - 캘린더에 반영.