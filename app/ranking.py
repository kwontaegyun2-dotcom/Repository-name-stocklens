# -*- coding: utf-8 -*-
"""주요 종목 실시간 랭킹 — 백그라운드로 채점·캐싱해 즉시 순위 제공."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app import naver, analysis, chart_pro, valuation, backtest

# (종목코드, 표시명, 섹터) — 코스피·코스닥 주요 130여 종목
UNIVERSE = [
    # 반도체
    ("005930", "삼성전자", "반도체"), ("000660", "SK하이닉스", "반도체"),
    ("009150", "삼성전기", "반도체"), ("042700", "한미반도체", "반도체"),
    ("000990", "DB하이텍", "반도체"), ("039030", "이오테크닉스", "반도체"),
    ("058470", "리노공업", "반도체"), ("240810", "원익IPS", "반도체"),
    ("036930", "주성엔지니어링", "반도체"), ("403870", "HPSP", "반도체"),
    ("064760", "티씨케이", "반도체"), ("005290", "동진쎄미켐", "반도체"),
    ("140860", "파크시스템스", "반도체"),
    # 2차전지
    ("373220", "LG에너지솔루션", "2차전지"), ("006400", "삼성SDI", "2차전지"),
    ("247540", "에코프로비엠", "2차전지"), ("086520", "에코프로", "2차전지"),
    ("003670", "포스코퓨처엠", "2차전지"), ("051910", "LG화학", "2차전지"),
    ("348370", "엔켐", "2차전지"), ("121600", "나노신소재", "2차전지"),
    ("005070", "코스모신소재", "2차전지"),
    # 바이오·제약
    ("207940", "삼성바이오로직스", "바이오·제약"), ("068270", "셀트리온", "바이오·제약"),
    ("196170", "알테오젠", "바이오·제약"), ("028300", "HLB", "바이오·제약"),
    ("000100", "유한양행", "바이오·제약"), ("128940", "한미약품", "바이오·제약"),
    ("006280", "녹십자", "바이오·제약"), ("185750", "종근당", "바이오·제약"),
    ("326030", "SK바이오팜", "바이오·제약"), ("145020", "휴젤", "바이오·제약"),
    ("096530", "씨젠", "바이오·제약"), ("298380", "에이비엘바이오", "바이오·제약"),
    ("141080", "리가켐바이오", "바이오·제약"), ("214450", "파마리서치", "바이오·제약"),
    ("214150", "클래시스", "바이오·제약"), ("328130", "루닛", "바이오·제약"),
    ("087010", "펩트론", "바이오·제약"),
    # 자동차·부품
    ("005380", "현대차", "자동차·부품"), ("000270", "기아", "자동차·부품"),
    ("012330", "현대모비스", "자동차·부품"), ("018880", "한온시스템", "자동차·부품"),
    ("204320", "HL만도", "자동차·부품"), ("011210", "현대위아", "자동차·부품"),
    ("086280", "현대글로비스", "자동차·부품"),
    # 인터넷·게임
    ("035420", "NAVER", "인터넷·게임"), ("035720", "카카오", "인터넷·게임"),
    ("259960", "크래프톤", "인터넷·게임"), ("251270", "넷마블", "인터넷·게임"),
    ("036570", "엔씨소프트", "인터넷·게임"), ("293490", "카카오게임즈", "인터넷·게임"),
    ("263750", "펄어비스", "인터넷·게임"), ("112040", "위메이드", "인터넷·게임"),
    ("462870", "시프트업", "인터넷·게임"), ("194480", "데브시스터즈", "인터넷·게임"),
    # 엔터·미디어
    ("352820", "하이브", "엔터·미디어"), ("035900", "JYP Ent.", "엔터·미디어"),
    ("041510", "에스엠", "엔터·미디어"), ("122870", "와이지엔터", "엔터·미디어"),
    ("035760", "CJ ENM", "엔터·미디어"),
    # 금융
    ("105560", "KB금융", "금융"), ("055550", "신한지주", "금융"),
    ("086790", "하나금융지주", "금융"), ("316140", "우리금융지주", "금융"),
    ("024110", "기업은행", "금융"), ("323410", "카카오뱅크", "금융"),
    ("138040", "메리츠금융지주", "금융"), ("175330", "JB금융지주", "금융"),
    ("138930", "BNK금융지주", "금융"),
    # 증권·보험
    ("000810", "삼성화재", "증권·보험"), ("032830", "삼성생명", "증권·보험"),
    ("005830", "DB손해보험", "증권·보험"), ("001450", "현대해상", "증권·보험"),
    ("006800", "미래에셋증권", "증권·보험"), ("016360", "삼성증권", "증권·보험"),
    ("039490", "키움증권", "증권·보험"), ("005940", "NH투자증권", "증권·보험"),
    ("071050", "한국금융지주", "증권·보험"),
    # 철강·소재
    ("005490", "POSCO홀딩스", "철강·소재"), ("010130", "고려아연", "철강·소재"),
    ("004020", "현대제철", "철강·소재"), ("103140", "풍산", "철강·소재"),
    # 에너지·화학
    ("015760", "한국전력", "에너지·화학"), ("096770", "SK이노베이션", "에너지·화학"),
    ("010950", "S-Oil", "에너지·화학"), ("011170", "롯데케미칼", "에너지·화학"),
    ("011780", "금호석유", "에너지·화학"), ("009830", "한화솔루션", "에너지·화학"),
    # 방산·조선·기계
    ("012450", "한화에어로스페이스", "방산·조선·기계"), ("329180", "HD현대중공업", "방산·조선·기계"),
    ("034020", "두산에너빌리티", "방산·조선·기계"), ("064350", "현대로템", "방산·조선·기계"),
    ("079550", "LIG넥스원", "방산·조선·기계"), ("047810", "한국항공우주", "방산·조선·기계"),
    ("042660", "한화오션", "방산·조선·기계"), ("009540", "HD한국조선해양", "방산·조선·기계"),
    ("267260", "HD현대일렉트릭", "방산·조선·기계"), ("241560", "두산밥캣", "방산·조선·기계"),
    ("454910", "두산로보틱스", "방산·조선·기계"), ("277810", "레인보우로보틱스", "방산·조선·기계"),
    # 건설
    ("000720", "현대건설", "건설"), ("006360", "GS건설", "건설"),
    ("375500", "DL이앤씨", "건설"), ("028050", "삼성E&A", "건설"),
    # 통신
    ("017670", "SK텔레콤", "통신"), ("030200", "KT", "통신"),
    ("032640", "LG유플러스", "통신"),
    # 유통·소비재
    ("090430", "아모레퍼시픽", "유통·소비재"), ("051900", "LG생활건강", "유통·소비재"),
    ("271560", "오리온", "유통·소비재"), ("004370", "농심", "유통·소비재"),
    ("097950", "CJ제일제당", "유통·소비재"), ("000080", "하이트진로", "유통·소비재"),
    ("139480", "이마트", "유통·소비재"), ("282330", "BGF리테일", "유통·소비재"),
    ("021240", "코웨이", "유통·소비재"), ("161390", "한국타이어앤테크놀로지", "유통·소비재"),
    # 지주회사 — 진단리포트(2026-08-31) 6번: 지주회사(순수 지배구조 회사, PER이
    # 산하 상장 자회사들의 합산가치 대비 할인되어 거래되는 경우가 흔함)와 실제
    # 영업회사를 같은 PER 비교군에 두면 왜곡된다는 지적. 예전 "지주·기타"에
    # LG전자(가전·전자 제조사)·삼성에스디에스(IT서비스)·KT&G(소비재 제조사)·
    # HMM(해운 영업회사)처럼 지주회사가 아닌 순수 영업회사가 섞여 있던 걸 실제
    # 사업 성격에 맞는 카테고리로 재배치하고, 진짜 지주회사만 이 카테고리에 남긴다.
    ("028260", "삼성물산", "지주회사"), ("034730", "SK", "지주회사"),
    ("000150", "두산", "지주회사"), ("006260", "LS", "지주회사"),
    ("000880", "한화", "지주회사"),
    # --- 확대분 (2026-08-10, 네이버 검색 API로 종목코드 검증 후 추가) ---
    ("011070", "LG이노텍", "반도체·부품"), ("213420", "덕산네오룩스", "반도체·부품"),
    ("357780", "솔브레인", "반도체·부품"), ("222800", "심텍", "반도체·부품"),
    ("007660", "이수페타시스", "반도체·부품"), ("077360", "덕산하이메탈", "반도체·부품"),
    ("032500", "케이엠더블유", "반도체·부품"), ("108320", "LX세미콘", "반도체·부품"),
    ("204270", "제이앤티씨", "반도체·부품"), ("122990", "와이솔", "반도체·부품"),
    ("084850", "아이티엠반도체", "반도체·부품"), ("131290", "티에스이", "반도체·부품"),
    ("033640", "네패스", "반도체·부품"), ("067310", "하나마이크론", "반도체·부품"),
    ("036540", "SFA반도체", "반도체·부품"), ("086390", "유니테스트", "반도체·부품"),
    ("098460", "고영", "반도체·부품"),
    ("011790", "SKC", "2차전지·소재"), ("093370", "후성", "2차전지·소재"),
    ("278280", "천보", "2차전지·소재"), ("066970", "엘앤에프", "2차전지·소재"),
    ("271940", "일진하이솔루스", "2차전지·소재"), ("365340", "성일하이텍", "2차전지·소재"),
    ("107600", "새빗켐", "2차전지·소재"), ("014680", "한솔케미칼", "2차전지·소재"),
    ("120110", "코오롱인더", "2차전지·소재"),
    ("302440", "SK바이오사이언스", "바이오·제약"), ("170900", "동아에스티", "바이오·제약"),
    ("249420", "일동제약", "바이오·제약"), ("235980", "메드팩토", "바이오·제약"),
    ("140410", "메지온", "바이오·제약"), ("085660", "차바이오텍", "바이오·제약"),
    ("137310", "에스디바이오센서", "바이오·제약"),
    ("402340", "SK스퀘어", "지주회사"), ("085620", "미래에셋생명", "증권·보험"),
    ("004990", "롯데지주", "지주회사"), ("180640", "한진칼", "지주회사"),
    ("006120", "SK디스커버리", "지주회사"),
    ("066570", "LG전자", "가전·전자"), ("018260", "삼성에스디에스", "IT서비스"),
    ("033780", "KT&G", "유통·소비재"), ("011200", "HMM", "물류·상사"),
    ("006040", "동원산업", "유통·소비재"), ("007070", "GS리테일", "유통·소비재"),
    ("192820", "코스맥스", "유통·소비재"), ("161890", "한국콜마", "유통·소비재"),
    ("383220", "F&F", "유통·소비재"), ("111770", "영원무역", "유통·소비재"),
    ("004170", "신세계", "유통·소비재"), ("069960", "현대백화점", "유통·소비재"),
    ("018290", "브이티", "유통·소비재"), ("049070", "인탑스", "유통·소비재"),
    ("257720", "실리콘투", "유통·소비재"),
    ("000120", "CJ대한통운", "물류·상사"), ("028670", "팬오션", "물류·상사"),
    ("001120", "LX인터내셔널", "물류·상사"),
    ("298040", "효성중공업", "방산·조선·기계"),
    ("064960", "SNT모티브", "자동차·부품"),
    ("009520", "포스코엠텍", "철강·소재"),
]

# 미국 주요 종목 (reutersCode, 표시명, 섹터)
US_UNIVERSE = [
    ("NVDA.O", "엔비디아", "반도체"), ("AVGO.O", "브로드컴", "반도체"),
    ("AMD.O", "AMD", "반도체"), ("QCOM.O", "퀄컴", "반도체"),
    ("TXN.O", "텍사스인스트루먼트", "반도체"), ("AMAT.O", "어플라이드머티어리얼즈", "반도체"),
    ("MU.O", "마이크론", "반도체"), ("INTC.O", "인텔", "반도체"),
    ("ARM.O", "암 홀딩스", "반도체"), ("SMCI.O", "슈퍼마이크로컴퓨터", "반도체"),
    ("AAPL.O", "애플", "빅테크·인터넷"), ("MSFT.O", "마이크로소프트", "빅테크·인터넷"),
    ("GOOGL.O", "알파벳", "빅테크·인터넷"), ("AMZN.O", "아마존", "빅테크·인터넷"),
    ("META.O", "메타", "빅테크·인터넷"), ("NFLX.O", "넷플릭스", "빅테크·인터넷"),
    ("ORCL.K", "오라클", "빅테크·인터넷"), ("CRM", "세일즈포스", "빅테크·인터넷"),
    ("ADBE.O", "어도비", "빅테크·인터넷"), ("PLTR.O", "팔란티어", "빅테크·인터넷"),
    ("SHOP.O", "쇼피파이", "빅테크·인터넷"), ("IBM", "IBM", "빅테크·인터넷"),
    ("CSCO.O", "시스코", "빅테크·인터넷"), ("ACN", "액센추어", "빅테크·인터넷"),
    ("V", "비자", "금융·핀테크"), ("MA", "마스터카드", "금융·핀테크"),
    ("JPM", "JP모간체이스", "금융·핀테크"), ("BAC", "뱅크오브아메리카", "금융·핀테크"),
    ("GS", "골드만삭스", "금융·핀테크"), ("AXP", "아메리칸익스프레스", "금융·핀테크"),
    ("PYPL.O", "페이팔", "금융·핀테크"), ("COIN.O", "코인베이스", "금융·핀테크"),
    ("TSLA.O", "테슬라", "전기차·소비"), ("HD", "홈디포", "전기차·소비"),
    ("MCD", "맥도날드", "전기차·소비"), ("NKE", "나이키", "전기차·소비"),
    ("WMT.O", "월마트", "전기차·소비"), ("COST.O", "코스트코", "전기차·소비"),
    ("KO", "코카콜라", "전기차·소비"), ("PEP.O", "펩시코", "전기차·소비"),
    ("PG", "P&G", "전기차·소비"), ("DIS", "월트디즈니", "전기차·소비"),
    ("UBER.K", "우버", "전기차·소비"), ("ABNB.O", "에어비앤비", "전기차·소비"),
    ("LLY", "일라이릴리", "헬스케어"), ("JNJ", "존슨앤드존슨", "헬스케어"),
    ("UNH", "유나이티드헬스", "헬스케어"), ("MRK", "머크", "헬스케어"),
    ("PFE", "화이자", "헬스케어"), ("ABBV.K", "애브비", "헬스케어"),
    ("MRNA.O", "모더나", "헬스케어"),
    ("XOM", "엑슨모빌", "에너지·산업"), ("CVX", "셰브론", "에너지·산업"),
    ("BA", "보잉", "에너지·산업"), ("CAT", "캐터필러", "에너지·산업"),
    ("GE", "GE에어로스페이스", "에너지·산업"), ("DELL.K", "델테크놀로지", "에너지·산업"),
    ("T", "AT&T", "통신"), ("VZ", "버라이존", "통신"),
    # --- 확대분 ---
    ("TSM", "TSMC", "반도체"), ("ASML.O", "ASML", "반도체"),
    ("MRVL.O", "마벨", "반도체"), ("LRCX.O", "램리서치", "반도체"),
    ("KLAC.O", "KLA", "반도체"), ("SNPS.O", "시놉시스", "반도체"),
    ("CDNS.O", "케이던스", "반도체"), ("NXPI.O", "NXP", "반도체"),
    ("ON.O", "온세미", "반도체"), ("MCHP.O", "마이크로칩", "반도체"),
    ("ANET.K", "아리스타네트웍스", "반도체"), ("MPWR.O", "모놀리식파워", "반도체"),
    ("GFS.O", "글로벌파운드리", "반도체"), ("STM", "ST마이크로", "반도체"),
    ("NOW", "서비스나우", "빅테크·인터넷"), ("INTU.O", "인튜이트", "빅테크·인터넷"),
    ("PANW.O", "팔로알토네트웍스", "빅테크·인터넷"), ("CRWD.O", "크라우드스트라이크", "빅테크·인터넷"),
    ("SNOW.K", "스노우플레이크", "빅테크·인터넷"), ("DDOG.O", "데이터독", "빅테크·인터넷"),
    ("NET", "클라우드플레어", "빅테크·인터넷"), ("ZS.O", "지스케일러", "빅테크·인터넷"),
    ("MDB.O", "몽고DB", "빅테크·인터넷"), ("TEAM.O", "아틀라시안", "빅테크·인터넷"),
    ("WDAY.O", "워크데이", "빅테크·인터넷"), ("SPOT.K", "스포티파이", "빅테크·인터넷"),
    ("RBLX.K", "로블록스", "빅테크·인터넷"), ("TTD.O", "트레이드데스크", "빅테크·인터넷"),
    ("APP.O", "앱러빈", "빅테크·인터넷"), ("DASH.O", "도어대시", "빅테크·인터넷"),
    ("MS", "모간스탠리", "금융·핀테크"), ("WFC", "웰스파고", "금융·핀테크"),
    ("C", "씨티그룹", "금융·핀테크"), ("SCHW.K", "찰스슈왑", "금융·핀테크"),
    ("BLK", "블랙록", "금융·핀테크"), ("BX", "블랙스톤", "금융·핀테크"),
    ("KKR", "KKR", "금융·핀테크"), ("SPGI.K", "S&P글로벌", "금융·핀테크"),
    ("ICE", "인터콘티넨탈익스체인지", "금융·핀테크"), ("CME.O", "CME그룹", "금융·핀테크"),
    ("PGR", "프로그레시브", "금융·핀테크"),
    ("ABT", "애보트", "헬스케어"), ("TMO", "써모피셔", "헬스케어"),
    ("DHR", "다나허", "헬스케어"), ("ISRG.O", "인튜이티브서지컬", "헬스케어"),
    ("AMGN.O", "암젠", "헬스케어"), ("GILD.O", "길리어드", "헬스케어"),
    ("VRTX.O", "버텍스", "헬스케어"), ("REGN.O", "리제네론", "헬스케어"),
    ("BSX", "보스턴사이언티픽", "헬스케어"), ("MDT", "메드트로닉", "헬스케어"),
    ("SYK", "스트라이커", "헬스케어"), ("CI", "시그나", "헬스케어"),
    ("CVS", "CVS헬스", "헬스케어"), ("BMY", "BMS", "헬스케어"),
    ("NVO", "노보노디스크", "헬스케어"),
    ("SBUX.O", "스타벅스", "전기차·소비"), ("CMG", "치폴레", "전기차·소비"),
    ("BKNG.O", "부킹홀딩스", "전기차·소비"), ("TJX", "TJX", "전기차·소비"),
    ("LOW", "로우스", "전기차·소비"), ("TGT", "타겟", "전기차·소비"),
    ("EL", "에스티로더", "전기차·소비"), ("MDLZ.O", "몬델리즈", "전기차·소비"),
    ("CL", "콜게이트", "전기차·소비"), ("MO", "알트리아", "전기차·소비"),
    ("PM", "필립모리스", "전기차·소비"), ("MNST.O", "몬스터", "전기차·소비"),
    ("F", "포드", "전기차·소비"), ("GM", "GM", "전기차·소비"),
    ("RIVN.O", "리비안", "전기차·소비"),
    ("HON.O", "허니웰", "에너지·산업"), ("UNP", "유니온퍼시픽", "에너지·산업"),
    ("UPS", "UPS", "에너지·산업"), ("RTX", "RTX", "에너지·산업"),
    ("LMT", "록히드마틴", "에너지·산업"), ("DE", "디어", "에너지·산업"),
    ("GD", "제너럴다이내믹스", "에너지·산업"), ("ETN", "이튼", "에너지·산업"),
    ("COP", "코노코필립스", "에너지·산업"), ("SLB", "슐럼버거", "에너지·산업"),
    ("EOG", "EOG리소시스", "에너지·산업"), ("MPC", "마라톤페트롤리엄", "에너지·산업"),
    ("LIN.O", "린데", "에너지·산업"), ("ADP.O", "ADP", "에너지·산업"),
    ("CMCSA.O", "컴캐스트", "통신"), ("TMUS.O", "T모바일", "통신"),
    ("CHTR.O", "차터커뮤니케이션", "통신"),
    # --- 확대분 (2026-08-10, 네이버 검색 API로 종목코드 검증 후 추가) ---
    # 진단리포트(2026-08-31) 6번 — "유통·소비재"(국내)와 "소비재·유통"(미국, 이 블록)이
    # 단어 순서만 다른 완전히 같은 분류였다. 라벨을 하나로 통일한다.
    ("AZO", "오토존", "유통·소비재"), ("ORLY.O", "오라일리오토모티브", "유통·소비재"),
    ("ROST.O", "로스스토어즈", "유통·소비재"), ("DG", "달러제너럴", "유통·소비재"),
    ("KR", "크로거", "유통·소비재"), ("SYY", "사이스코", "유통·소비재"),
    ("ADM", "아처대니얼스미들랜드", "유통·소비재"),
    ("CTVA.K", "코르테바", "소재·화학"), ("DD", "듀폰", "소재·화학"),
    ("APD", "에어프로덕츠", "소재·화학"), ("ECL", "에콜랩", "소재·화학"),
    ("SHW", "셔윈윌리엄스", "소재·화학"), ("PPG", "PPG인더스트리즈", "소재·화학"),
    ("NUE", "뉴코", "소재·화학"), ("FCX", "프리포트맥모란", "소재·화학"),
    ("NEM", "뉴몬트", "소재·화학"),
    ("NEE", "넥스트에라에너지", "유틸리티"), ("DUK", "듀크에너지", "유틸리티"),
    ("D", "도미니언에너지", "유틸리티"),
    ("AMT", "아메리칸타워", "리츠·부동산"), ("PLD", "프로로지스", "리츠·부동산"),
    ("EQIX.O", "에퀴닉스", "리츠·부동산"), ("DLR", "디지털리얼티트러스트", "리츠·부동산"),
    ("SPG", "사이먼프로퍼티그룹", "리츠·부동산"), ("PSA", "퍼블릭스토리지", "리츠·부동산"),
    ("O", "리얼티인컴", "리츠·부동산"),
    ("MAR.O", "메리어트인터내셔널", "여행·레저"), ("HLT", "힐튼월드와이드", "여행·레저"),
    ("LUV", "사우스웨스트항공", "여행·레저"), ("DAL", "델타항공", "여행·레저"),
    ("NSC", "노퍽서던", "운송·산업재"), ("FDX", "페덱스", "운송·산업재"),
    ("MMM", "3M", "운송·산업재"), ("ITW", "일리노이툴웍스", "운송·산업재"),
    ("EMR", "에머슨일렉트릭", "운송·산업재"), ("CTAS.O", "신타스", "운송·산업재"),
    ("WM", "웨이스트매니지먼트", "운송·산업재"), ("RSG", "리퍼블릭서비스", "운송·산업재"),
    ("FTNT.O", "포티넷", "빅테크·인터넷"), ("OKTA.O", "옥타", "빅테크·인터넷"),
    ("TWLO.K", "트윌리오", "빅테크·인터넷"), ("HUBS.K", "허브스팟", "빅테크·인터넷"),
    ("ADSK.O", "오토데스크", "빅테크·인터넷"),
]

UNIVERSES = {"KR": UNIVERSE, "US": US_UNIVERSE}
_lock = threading.Lock()
_state = {
    "KR": {"items": [], "updated_at": 0, "computing": False, "stale": False},
    "US": {"items": [], "updated_at": 0, "computing": False, "stale": False},
}
# 2026-09-14 속도 진단 — 전 종목 재채점(technical_analysis·chart_pro·fundamental)의
# 입력값은 일봉 캔들·분기 재무제표라 실제로는 하루~분기 단위로만 바뀐다. 그런데도
# 30분마다 372종목 전체를 처음부터 다시 계산해 CPU를 태워왔고(오라클 인스턴스가
# CPU 스틸타임 70%대인 빈약한 공유 VM이라 이 부하가 그대로 /api/analyze 등 다른
# 요청 지연으로 번짐 — 실측: 재계산 도중 analyze 23초대). 가격·등락률은 이미 별도
# _price_loop(60초)가 가볍게 담당하므로, 점수 자체의 갱신 주기를 늘려도 "방금 산
# 가격"이 안 보이는 문제는 없다 — 2시간으로 늘려 재계산 빈도(=CPU 부하 발생 빈도)를
# 4배 줄인다.
REFRESH_SEC = 7200  # 2시간마다 갱신 (기존 30분 — 점수 입력이 일봉/분기 단위라 과했음)
_cache_path = {"KR": None, "US": None}


def init(data_dir):
    """진단리포트(2026-08-31) UX 1번 — 배포·재시작 직후 랭킹이 처음부터 다시 계산되는
    동안 첫 방문자가 몇십 초~몇 분씩 빈 화면을 보는 문제. 매 계산 결과를 디스크(코드
    배포 경로 밖 STOCKLENS_DATA_DIR — auth.py의 users.db와 동일 패턴)에 스냅샷으로
    남겨뒀다가, 프로세스 시작 시 즉시 불러와 백그라운드 재계산이 끝나기 전에도 "지난
    결과"를 바로 보여준다. 신선도는 `stale` 플래그 + updated_at으로 프론트에 그대로
    노출(억지로 최신인 척하지 않는다 — 이 프로젝트 일관 원칙)."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    for market in ("KR", "US"):
        path = data_dir / f"ranking_cache_{market}.json"
        _cache_path[market] = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("items"):
                _state[market]["items"] = raw["items"]
                _state[market]["updated_at"] = raw.get("updated_at", 0)
                _state[market]["stale"] = True
        except Exception:
            pass


def _safe(fn, d):
    try:
        return fn()
    except Exception:
        return d


def _score(entry, market, bench=None):
    code, disp, sector = entry
    try:
        us = market == "US"
        b = naver.basic(code)
        name = b.get("stockName", disp)
        price = analysis.to_num(b.get("closePrice"))
        rate = analysis.to_num(b.get("fluctuationsRatio"))
        currency = (b.get("currencyType") or {}).get("code") or "KRW"

        # 검색(=/api/analyze)과 점수 정합성을 위해 동일한 입력·계산을 쓴다.
        # 캔들 1300개(52주 위치·장기 이평), 뉴스 3건, 고급 차트분석(pro)까지 동일.
        integ = _safe(lambda: naver.integration(code), {})
        fin_a = _safe(lambda: naver.finance(code, "annual"), {})
        news = _safe(lambda: naver.news(code, 3), [])
        trend = _safe(lambda: naver.trend(code), [])
        candles = _safe(lambda: naver.candles(code, 1300), [])

        src = (b.get("stockItemTotalInfos") if us else integ.get("totalInfos")) or []
        infos = {i.get("code"): i.get("value") for i in src}

        tech = analysis.technical_analysis(candles)
        pro = _safe(lambda: chart_pro.analyze(candles, bench), {"available": False})
        fund = analysis.fundamental_analysis(infos, fin_a, market=market)
        senti = analysis.news_sentiment(news, stock_name=name)
        cons = analysis.consensus_info(integ, price)
        total = analysis.total_evaluation(fund, tech, senti, cons, trend, pro)
        # 밸류에이션 전체 재계산(peers_per 조회 등)은 무거워서 랭킹에서는 생략하고,
        # 이미 있는 total·cons만으로 확신도를 계산한다(analyze()보다 신호가 적어
        # 다소 보수적으로 나올 수 있음 — main.py 상세페이지는 valuation까지 반영).
        ai_verdict = analysis.final_verdict(total, cons=cons)

        # 이상징후 탐지(app/anomaly.py)가 재사용할 원신호. 여기서 이미 확보한 데이터로만
        # 계산해서 추가 네트워크 호출이 없다(전체(rank) 재계산 주기 부담을 늘리지 않음).
        per_ratio = None
        per_basis = None   # "fwd"(선행PER, 컨센서스 EPS 기준) | "trail"(과거 실적 기준) — 홈 화면 사유 문구용
        hist = _safe(lambda: valuation.per_history(fund.get("all_rows") or {}, candles), None)
        if hist:
            m = fund["metrics"]
            use_fwd = bool(m.get("cns_per") and m["cns_per"] > 0)
            cur = m.get("cns_per") if use_fwd else m.get("per")
            avg = hist["avg_fper"] if (use_fwd and hist.get("avg_fper")) else hist.get("avg_per")
            if cur and cur > 0 and avg and avg > 0:
                per_ratio = round(cur / avg, 2)
                per_basis = "fwd" if (use_fwd and hist.get("avg_fper")) else "trail"

        foreign_dir = None
        if not us and trend:
            flows = [analysis.to_num(t.get("foreignerPureBuyQuant")) for t in trend[:5]]
            flows = [f for f in flows if f is not None]
            if len(flows) >= 3:
                if all(f > 0 for f in flows):
                    foreign_dir = "buy"
                elif all(f < 0 for f in flows):
                    foreign_dir = "sell"

        fm = fund["metrics"]
        return {
            "code": code, "name": name, "sector": sector,
            "price": price, "rate": rate, "currency": currency,
            "score": total["total_score"], "grade": total["grade"],
            "grade_desc": total["grade_desc"],
            "categories": total["categories"],
            "upside": cons.get("upside"),
            # 목표주가 괴리가 커(analysis.TARGET_UPSIDE_OUTLIER=60%) 상세페이지에서는
            # 이미 판단 반영 비중을 낮추던 값인데, 랭킹/스크리너/포트폴리오는 이 플래그
            # 없이 원본 upside를 그대로 노출하고 있었다(UI/UX 검증보고서 6-7 — 포트폴리오가
            # D등급이라면서 기대수익률 +70.8%를 아무 경고 없이 보여준 근본 원인). 새 계산
            # 없이 consensus_info()가 이미 만들어둔 값을 그대로 실어 보내기만 하면 된다.
            "upside_flagged": cons.get("upside_flagged"),
            "target_price": cons.get("target_price"),
            "ai_verdict": ai_verdict,
            # ⚠️ 예전엔 "verdict"(tech.get("verdict"), 단기 진입타이밍)도 여기서 함께
            # 내려보냈다 — UI/UX 검증보고서(2026-08-21) 6-1이 지적: ai_verdict(펀더멘털
            # 종합)와 verdict(단기 기술)는 서로 다른 걸 측정하는 지표라 값이 늘 다르고,
            # 랭킹/스크리너 응답에 라벨 없이 나란히 실리면 "서버가 판단을 두 개 내려보낸다"
            # 는 모순으로 읽힌다. 실제로 클라이언트 어느 리스트 렌더러도 이 필드를 읽지
            # 않는 죽은 페이로드였음(확인 완료) — 그래서 되살리지 말고 제거. 상세페이지
            # (main.py)의 tech.verdict는 "언제 살까" 라벨과 함께 별도 카드에 여전히 노출됨,
            # 그건 그대로 유지(HANDOFF 3.11 — 두 지표를 억지로 합치면 오히려 부정확해짐).
            "rsi": tech.get("rsi") if tech.get("available") else None,
            "op_growth_fwd": fm.get("op_growth_fwd"),
            "per_ratio": per_ratio,
            "per_basis": per_basis,
            "foreign_dir": foreign_dir,
            # 스크리너(app/screener.py)가 추가 네트워크 호출 없이 조건 필터링만 하도록
            # fundamental_analysis()가 이미 계산해둔 지표를 그대로 실어 보낸다.
            "per": fm.get("per"), "pbr": fm.get("pbr"), "roe": fm.get("roe"),
            "debt_ratio": fm.get("debt_ratio"), "dividend_yield": fm.get("dividend_yield"),
            "market_cap": fm.get("market_cap"), "market": market,
        }
    except Exception:
        return None


def _publish(st, out, market=None):
    snap = sorted(out, key=lambda x: x["score"], reverse=True)
    for i, r in enumerate(snap, 1):
        r["rank"] = i
    updated_at = time.time()
    with _lock:
        st["items"] = snap
        st["updated_at"] = updated_at
        st["stale"] = False   # 이번 프로세스에서 실제로 새로 계산한 데이터로 교체됨
    path = _cache_path.get(market) if market else None
    if path:
        try:
            path.write_text(json.dumps({"items": snap, "updated_at": updated_at}, ensure_ascii=False),
                             encoding="utf-8")
        except Exception:
            pass


PRICE_REFRESH_SEC = 60  # 등락률만 자주 갱신 — 전체 재계산(30분)보다 훨씬 자주


def _refresh_prices(market):
    # 전체 분석(_score)은 30분 주기라 장 시작 직후처럼 직전 계산이 개장 전 스냅샷이면
    # 최대 30분간 등락률이 0%로 굳어 보인다(진단 리포트 3-5). 가격·등락률만은 가벼운
    # 단일 호출(naver.basic, ttl=2초)이라 자주 돌려도 부담이 없으므로 별도 루프로
    # 훨씬 촘촘하게 갱신해 점수·등급은 그대로 두고 표시값만 최신으로 맞춘다.
    st = _state[market]
    with _lock:
        items = list(st["items"])
    if not items:
        return
    by_code = {}

    def fetch(code):
        b = _safe(lambda: naver.basic(code), None)
        return code, b

    with ThreadPoolExecutor(max_workers=8) as ex:
        for code, b in ex.map(fetch, [it["code"] for it in items]):
            if b:
                by_code[code] = b
    with _lock:
        changed = False
        for it in st["items"]:
            b = by_code.get(it["code"])
            if not b:
                continue
            quote = analysis.effective_quote(b)
            price = quote["price"]
            rate = quote["rate"]
            if price is not None:
                it["price"] = price
            if rate is not None:
                it["rate"] = rate
                changed = True
        if changed:
            st["updated_at"] = time.time()


def _compute(market):
    st = _state[market]
    with _lock:
        if st["computing"]:
            return
        st["computing"] = True
    try:
        # 상대강도 벤치마크를 1회만 조회해 전 종목에 공유 (analyze와 동일 기준).
        if market == "US":
            bench = _safe(lambda: [c["close"] for c in naver.candles("SPY", 1300)], [])
        else:
            bench = _safe(lambda: naver.index_candles("KOSPI", 1300), [])
        out = []
        total = len(UNIVERSES[market])
        # 전부 끝날 때까지 화면이 텅 비어있지 않도록, 완료되는 대로 주기적으로 중간 결과를
        # 공개한다(20종목마다). 종목 수가 늘면서(미국 190개) 전체 완료까지 몇 분씩 걸릴 수
        # 있어, 다 끝난 뒤에야 한번에 보여주면 사용자가 계속 빈 스피너만 보고 이탈하게 된다.
        # 동시 요청 수가 너무 높으면(예전 16) 서버 재시작 직후 전체 재계산이 개별 종목
        # analyze() 요청과 네트워크 대역폭을 다퉈서 몇십 초씩 느려지거나 타임아웃난다
        # (2026-08-13 실측: 재배포 직후 analyze가 19~30초까지 늘어졌다가, 랭킹 계산이
        # 끝나자마자 1초 미만으로 즉시 복귀 — 랭킹 병렬도를 낮춰 완화).
        # 2026-09-14 재측정 — 8로 낮춘 뒤에도 재계산 도중 analyze가 23초대까지 나옴
        # (오라클 인스턴스가 CPU 스틸타임 70%대인 빈약한 공유 VM이라 네트워크 대기가
        # 아니라 종목별 기술적분석·chart_pro 연산 자체가 CPU를 다툰다). start_background()의
        # 지연 기동과 별개로, 재계산이 실제로 도는 동안의 순간 부하 자체도 낮춘다.
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = [ex.submit(_score, e, market, bench) for e in UNIVERSES[market]]
            for i, fut in enumerate(as_completed(futures), 1):
                r = fut.result()
                if r:
                    out.append(r)
                if out and (i % 20 == 0 or i == total):
                    _publish(st, out, market)
        # 전체 계산이 끝난 뒤(완전한 out)에만 백테스트 스냅샷을 남긴다 — 하루 한 번만
        # 실제로 기록되므로(app/backtest.py가 idempotent 체크) 30분마다 불려도 무방하다.
        _safe(lambda: backtest.snapshot(market, out), None)
    finally:
        with _lock:
            st["computing"] = False


def _loop(market, initial_delay=0):
    if initial_delay:
        time.sleep(initial_delay)
    while True:
        _compute(market)
        time.sleep(REFRESH_SEC)


def _price_loop(market, initial_delay=0):
    if initial_delay:
        time.sleep(initial_delay)
    while True:
        _safe(lambda: _refresh_prices(market), None)
        time.sleep(PRICE_REFRESH_SEC)


def start_background():
    # 국내는 즉시, 미국은 90초 지연 후 자동 시작 — 둘 다 서버 기동 시 스스로 도는 구조로
    # 바꿔서, 사용자가 미국 탭을 처음 열 때부터 집계를 기다리는 일이 없게 한다(예전엔
    # 첫 요청이 있어야 시작해서, 최초 방문자가 전체 계산 시간을 그대로 떠안았음).
    # 90초 지연은 국내 집계와 동시에 시작해 부하가 몰리는 것만 피하기 위함.
    #
    # ⚠️ 2026-09-14 속도 진단 — 디스크 캐시(init()에서 로드)가 이미 있는데도 위 지연은
    # "즉시 재계산"까지만 미룰 뿐이라, 매 배포(재시작)마다 372종목 전체 재채점이 그
    # 순간부터 바로 터졌다. 방금 재배포 직후 /api/analyze가 23.7초, 랭킹 API 응답이
    # 8분 뒤에도 KR 140/182종목만 끝난 상태로 실측됨 — "재배포 직후 지연"이라고
    # 여러 진단리포트에 반복 기록된 현상의 실제 발생 지점이 바로 이 즉시-재계산이었다.
    # 캐시가 이미 있으면(=첫 콜드스타트가 아니면) 전체 재채점을 몇 분 늦춰, 배포 직후
    # 트래픽이 가라앉을 시간을 준다 — 그동안은 캐시된 점수 + _price_loop의 시세만
    # 갱신되므로 "약간 오래된 점수, 하지만 정확한 실시간가"로 서비스는 계속된다.
    kr_delay = 0 if not _state["KR"]["items"] else 240
    us_delay = 90 if not _state["US"]["items"] else 300
    threading.Thread(target=_loop, args=("KR", kr_delay), daemon=True).start()
    threading.Thread(target=_loop, args=("US", us_delay), daemon=True).start()
    threading.Thread(target=_price_loop, args=("KR", 20), daemon=True).start()
    threading.Thread(target=_price_loop, args=("US", 110), daemon=True).start()


def get(market: str = "KR", sector: str = None):
    market = "US" if str(market).upper() == "US" else "KR"
    st = _state[market]
    with _lock:
        items = list(st["items"])
        meta = {"updated_at": st["updated_at"], "computing": st["computing"], "stale": st["stale"]}
    # 미국 첫 로드: 아직 비어 있으면 집계중으로 표시(프론트가 폴링)
    if market == "US" and not items:
        meta["computing"] = True
    sectors = sorted({r["sector"] for r in items})
    if sector and sector != "전체":
        items = [r for r in items if r["sector"] == sector]
    return {"items": items, "sectors": sectors, "market": market, **meta}
