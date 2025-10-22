import streamlit as st
import pandas as pd
import plotly.express as px
from ortools.sat.python import cp_model
import sys
import os
import datetime # 오늘 날짜를 가져오기 위해 import

# -----------------------------------------------------------------
# [1. APS 스케줄링 최적화 엔진 함수]
# (bar_text 생성 부분 수정됨)
# -----------------------------------------------------------------
def solve_job_shop_scheduling(jobs_data, all_machines):
    """
    APS 스케줄링 문제를 풀어 최적의 스케줄을 반환합니다.
    (안정화된 우선순위 가중치 기반 목표 함수 적용)
    """

    num_jobs = len(jobs_data)
    num_machines = len(all_machines)

    model = cp_model.CpModel()

    tasks = {}
    task_intervals = {}
    for machine in all_machines:
        task_intervals[machine] = []

    try:
        horizon = int(sum(task['duration'] for job in jobs_data.values() for task in job))
    except Exception as e:
        st.error(f"❌ 오류: '소요시간(H)' 열 합계 계산 중 오류: {e}")
        st.stop()

    if horizon == 0:
        st.error("❌ 오류: 유효한 작업 시간이 없습니다. '소요시간(H)' 열을 확인하세요.")
        st.stop()

    all_priorities = [tasks[0]['priority'] for tasks in jobs_data.values()]
    if not all_priorities:
        st.error("❌ 오류: 우선순위 데이터를 찾을 수 없습니다.")
        st.stop()

    max_priority_level = max(all_priorities)
    min_priority_level = min(all_priorities)

    all_last_tasks = []

    for job_name, job_tasks in jobs_data.items():
        for i, task_info in enumerate(job_tasks):
            machine_name = task_info['machine']
            duration = int(task_info['duration'])

            task_name = f"{job_name}_공정_{i}"

            start_var = model.NewIntVar(0, horizon, f'{task_name}_start')
            end_var = model.NewIntVar(0, horizon, f'{task_name}_end')

            interval_var = model.NewIntervalVar(start_var, duration, end_var, f'{task_name}_interval')

            tasks[(job_name, i)] = {
                'start': start_var,
                'end': end_var,
                'interval': interval_var,
                'machine': machine_name,
                'duration': duration,
                'task_name_disp': task_info.get('display_name', 'task'), # 제품명
                'batch_no': task_info.get('batch_no', ''), # 제조번호
                'priority': task_info.get('priority', max_priority_level)
            }
            task_intervals[machine_name].append(interval_var)

        all_last_tasks.append(tasks[(job_name, len(job_tasks) - 1)]['end'])

    for machine_name in all_machines:
        model.AddNoOverlap(task_intervals[machine_name])

    for job_name, job_tasks in jobs_data.items():
        for i in range(len(job_tasks) - 1):
            task_before = tasks[(job_name, i)]
            task_after = tasks[(job_name, i + 1)]
            model.Add(task_after['start'] >= task_before['end'])


    makespan = model.NewIntVar(0, horizon, 'makespan')
    model.AddMaxEquality(makespan, all_last_tasks)

    objective_terms = []

    for job_name, job_tasks in jobs_data.items():
        last_task_end_var = tasks[(job_name, len(job_tasks) - 1)]['end']
        priority = job_tasks[0]['priority']
        simple_weight = (max_priority_level - priority) + 1
        # P1 가중치 강화
        weight = simple_weight ** 3

        objective_terms.append(last_task_end_var * weight)

    model.Minimize(sum(objective_terms))

    print(f"--- 우선순위 가중치 강화 (P1^3) 스케줄링 활성화 ---")

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 60.0 # <-- 시간제한 60초
    print(f"--- 솔버 시간제한 60초 설정 ---")

    status = solver.Solve(model)

    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        print(f"\n✅ 스케줄을 찾았습니다! (Makespan: {solver.Value(makespan)})")
        schedule_results = []
        for job_name, job_tasks in jobs_data.items():
            for i in range(len(job_tasks)):
                task = tasks[(job_name, i)]
                start_time = solver.Value(task['start'])
                end_time = solver.Value(task['end'])

                # --- [✨ 막대 텍스트 조합 (줄 바꿈 적용) ✨] ---
                # '/' 대신 HTML 줄바꿈 태그 '<br>' 사용
                bar_text = f"{job_name}<br>{task['task_name_disp']}<br>{task['batch_no']}"
                # --- [수정 완료] ---

                schedule_results.append(dict(
                    Job=job_name, # 오더번호
                    Machine=task['machine'],
                    Start=start_time,
                    Finish=end_time,
                    Duration=task['duration'],
                    Task=task['task_name_disp'], # 제품명
                    BatchNo=task['batch_no'], # 제조번호
                    BarText=bar_text # 조합된 텍스트 (줄바꿈 포함)
                ))
        return schedule_results, solver.Value(makespan)
    else:
        print(f"\n❌ 스케줄을 찾지 못했습니다. (상태 코드: {status})")
        st.error(f"❌ 최적의 스케줄을 찾지 못했습니다. (솔버 상태: {status})")
        st.warning("데이터에 무리한 제약이 없는지, '우선순위' 또는 '소요시간' 열에 비정상적인 값이 없는지 확인하세요.")
        return None, None

# -----------------------------------------------------------------
# [2. 데이터 로드 및 처리 함수 (Streamlit 캐시 적용)]
# -----------------------------------------------------------------

@st.cache_data
def load_and_parse_data(excel_path, sheet_name, cols_map):
    """
    엑셀 파일을 로드하고 스케줄링에 필요한 형식으로 파싱합니다.
    """
    print("--- 엑셀 데이터 로드 및 파싱 실행 ---")

    df = pd.read_excel(excel_path, sheet_name=sheet_name)

    required_cols = list(cols_map.values())
    if not all(col in df.columns for col in required_cols):
        raise FileNotFoundError(f"엑셀에 필수 열({required_cols})이 없습니다. 현재 열: {df.columns.tolist()}")

    df = df.dropna(subset=[cols_map['duration'], cols_map['priority']])
    df = df[df[cols_map['duration']] > 0]

    try:
        df[cols_map['priority']] = df[cols_map['priority']].astype(int)
    except ValueError:
        raise ValueError(f"'{cols_map['priority']}' 열에 숫자가 아닌 값이 포함되어 있습니다.")

    try:
        df[cols_map['step']] = pd.to_numeric(df[cols_map['step']], errors='coerce')
        df = df.dropna(subset=[cols_map['step']]) # 숫자로 변환 안되면 제거
        df[cols_map['step']] = df[cols_map['step']].astype(int)
    except Exception as e:
         raise ValueError(f"'{cols_map['step']}' 열을 숫자로 변환하는 데 실패했습니다: {e}")


    if df.empty:
        return None, None, None

    df = df.sort_values(by=[cols_map['id'], cols_map['step']])

    jobs_data_parsed = {}
    for job_name, group_df in df.groupby(cols_map['id']):
        tasks_list = []

        first_priority = int(group_df.iloc[0][cols_map['priority']])
        # 제조번호가 비어있을 경우 대비 (fillna('') 추가)
        first_batch_no = str(group_df.iloc[0][cols_map['batch']]) if not pd.isna(group_df.iloc[0][cols_map['batch']]) else ''


        for index, row in group_df.iterrows():
            display_name = row.get(cols_map['display'])
            if pd.isna(display_name):
                display_name = str(job_name)

            tasks_list.append({
                'machine': row[cols_map['machine']],
                'duration': row[cols_map['duration']],
                'display_name': display_name, # 제품명
                'batch_no': first_batch_no,   # 제조번호
                'priority': first_priority
            })
        jobs_data_parsed[str(job_name)] = tasks_list # 오더번호를 문자열 키로 사용

    all_machines = df[cols_map['machine']].dropna().unique().tolist()

    return jobs_data_parsed, all_machines, df

@st.cache_data
def run_solver(jobs_data, all_machines):
    """
    캐시된 스케줄링 엔진을 실행합니다.
    """
    print("--- APS 스케줄링 최적화 엔진 실행 ---")
    return solve_job_shop_scheduling(jobs_data, all_machines)

# -----------------------------------------------------------------
# [3. Streamlit 웹 애플리케이션 메인 로직]
# -----------------------------------------------------------------

def run_app():

    # --- 1. 기본 설정 ---
    st.set_page_config(layout="wide")
    col1, col2 = st.columns([1, 5])
    try:
        logo_filename = 'logo.png'
        script_directory = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(script_directory, logo_filename)
        col1.image(logo_path, width=70)
    except NameError:
         try:
             logo_filename = 'logo.png'
             logo_path = os.path.join(os.getcwd(), logo_filename)
             col1.image(logo_path, width=70)
         except Exception as e:
            col1.write("")
            print(f"로고 로드 실패: {e}")
    except Exception as e:
        col1.write("")
        print(f"로고 로드 실패: {e}")
    col2.title("AJUPHARM-APS")


    excel_filename = 'pop_data.xlsx'
    try:
        script_directory = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        script_directory = os.getcwd()
    EXCEL_FILE_PATH = os.path.join(script_directory, excel_filename)

    EXCEL_SHEET_NAME = 'Sheet1'

    COLS_MAP = {
        'id': '오더번호',
        'step': '공정',
        'machine': '설비명',
        'duration': '소요시간(H)',
        'display': '제품명',
        'department': '부서명',
        'priority': '우선순위',
        'batch': '제조번호'
    }

    # 오늘 날짜 기준으로 시작 시간 설정
    today_start = pd.Timestamp.now().floor('D')
    PROJECT_START_TIME = today_start + pd.Timedelta(hours=8, minutes=30)


    # --- 2. 데이터 로드 및 스케줄링 실행 (캐시 활용) ---
    try:
        jobs_data, all_machines, df_raw = load_and_parse_data(EXCEL_FILE_PATH, EXCEL_SHEET_NAME, COLS_MAP)

        if jobs_data is None:
             st.error("스케줄링할 데이터가 없습니다. (엑셀의 '소요시간(H)', '우선순위', '공정' 열 확인)")
             st.stop()

        results, makespan = run_solver(jobs_data, all_machines)

        if results is None:
            st.stop()

    except FileNotFoundError as e:
        st.error(f"❌ 오류: {e}")
        st.info("스크립트와 엑셀 파일이 동일한 폴더에 있는지, 열 이름이 정확한지 확인하세요.")
        st.stop()
    except Exception as e:
        st.error(f"❌ 데이터 로드/검증 중 오류: {e}")
        st.stop()

    # --- 3. UI 위젯 생성 (사이드바) ---
    st.sidebar.header("🗓️ 스케줄링 기준")
    scheduling_start_date = st.sidebar.date_input(
        "스케줄링 시작 날짜:",
        value=datetime.date.today()
    )
    PROJECT_START_TIME = pd.to_datetime(scheduling_start_date) + pd.Timedelta(hours=8, minutes=30)


    st.sidebar.header("📊 뷰 옵션")
    view_days = st.sidebar.number_input(
        "표시할 일 수 (Days):",
        min_value=1,
        value=5, # 기본 5일
        step=1
    )
    start_date = st.sidebar.date_input(
        "차트 조회 시작 날짜:",
        value=scheduling_start_date # 기본값을 스케줄링 시작 날짜로
    )

    st.sidebar.header("⚙️ 데이터 필터")

    # (연동 필터 1: 부서명)
    all_departments = df_raw[COLS_MAP['department']].dropna().unique().tolist()
    with st.sidebar.expander("부서명 필터", expanded=False):
        selected_departments = st.multiselect(
            "부서 선택:",
            options=sorted(all_departments),
            default=all_departments,
            label_visibility="collapsed"
        )

    # (연동 필터 2: 제품명)
    relevant_products = df_raw[df_raw[COLS_MAP['department']].isin(selected_departments)][COLS_MAP['display']].dropna().unique().tolist()
    with st.sidebar.expander("제품명 필터", expanded=False):
        selected_products = st.multiselect(
            "제품 선택:",
            options=sorted(relevant_products),
            default=relevant_products,
            label_visibility="collapsed"
        )

    # (연동 필터 3: 오더번호)
    relevant_orders = df_raw[
        (df_raw[COLS_MAP['department']].isin(selected_departments)) &
        (df_raw[COLS_MAP['display']].isin(selected_products))
    ][COLS_MAP['id']].dropna().unique().tolist()
    relevant_orders_str = sorted([str(o) for o in relevant_orders])

    with st.sidebar.expander("오더번호 필터", expanded=False):
        selected_orders = st.multiselect(
            "오더 선택:",
            options=relevant_orders_str,
            default=relevant_orders_str,
            label_visibility="collapsed"
        )


    # (연동 필터 4: 설비명)
    relevant_machines = df_raw[
        (df_raw[COLS_MAP['department']].isin(selected_departments)) &
        (df_raw[COLS_MAP['display']].isin(selected_products)) &
        (df_raw[COLS_MAP['id']].astype(str).isin(selected_orders))
    ][COLS_MAP['machine']].dropna().unique().tolist()

    with st.sidebar.expander("설비 필터", expanded=False):
        selected_machines = st.multiselect(
            "설비 선택:",
            options=sorted(relevant_machines),
            default=relevant_machines,
            label_visibility="collapsed"
        )

    st.sidebar.info(f"총 {len(jobs_data)}개 오더\n\n총 {makespan}시간 소요\n(우선순위 적용됨)")

    # --- 4. 간트 차트 생성 및 필터링 ---

    df_results = pd.DataFrame(results) # 'BarText' 포함됨
    df_results['Start_dt'] = PROJECT_START_TIME + pd.to_timedelta(df_results['Start'], unit='h')
    df_results['Finish_dt'] = PROJECT_START_TIME + pd.to_timedelta(df_results['Finish'], unit='h')

    # 차트 X축 범위
    start_datetime_chart = pd.to_datetime(start_date)
    start_datetime_view = start_datetime_chart.floor('D')
    end_datetime_view = start_datetime_view + pd.to_timedelta(view_days, unit='d')


    # (필터링 로직)
    # [✨ Merge 정보에 '제조번호' 추가 확인 ✨]
    merge_cols = [COLS_MAP['id'], COLS_MAP['department'], COLS_MAP['display'], COLS_MAP['priority'], COLS_MAP['batch']]
    # id 열을 문자열로 변환하여 merge 준비
    info_map = df_raw[merge_cols].drop_duplicates(subset=[COLS_MAP['id']]).astype({COLS_MAP['id']: str})


    df_results_with_info = pd.merge(
        df_results,
        info_map,
        left_on='Job',
        right_on=COLS_MAP['id'],
        how='left'
    )

    df_filtered = df_results_with_info[
        (df_results_with_info['Machine'].isin(selected_machines)) &
        (df_results_with_info[COLS_MAP['department']].isin(selected_departments)) &
        (df_results_with_info[COLS_MAP['display']].isin(selected_products)) &
        (df_results_with_info['Job'].isin(selected_orders))
    ]

    if df_filtered.empty:
        st.warning("선택한 필터에 해당하는 데이터가 없습니다.")
    else:
        # (Y축 정렬 로직)
        min_step_by_machine = df_raw.groupby(COLS_MAP['machine'])[COLS_MAP['step']].min().fillna(9999)
        sorted_selected_machines = sorted(
            selected_machines,
            key=lambda machine: min_step_by_machine.get(machine, 9999)
        )

        # 4-2. 간트 차트 생성
        fig = px.timeline(
            df_filtered,
            x_start="Start_dt",
            x_end="Finish_dt",
            y="Machine",
            color="Task", # 범례: 제품명
            text="BarText",  # <-- [✨ 막대 텍스트를 줄바꿈 포함된 BarText로 ✨]
            title=f"APS 스케줄링 결과 (총 {makespan}시간)",
            hover_data=[COLS_MAP['priority'], COLS_MAP['batch']]
        )

        fig.update_traces(textposition='inside')

        # (막대 늘어남 방지 및 날짜/시간 형식, 구분선)
        chart_height = (len(selected_machines) * 50) + 150

        fig.update_layout(
            height=chart_height,
            yaxis=dict(
                tickfont=dict(size=14),
                categoryorder="array",
                categoryarray=sorted_selected_machines[::-1] # 오름차순으로 보이게 역순
            ),
            xaxis=dict(
                title_text="스케줄 시간",
                tickfont=dict(size=12),
                range=[start_datetime_view, end_datetime_view],
                rangeslider=dict(visible=True),
                side='top',
                tickformat='%y-%m-%d<br>%H:%M',
                gridcolor='gray',
                gridwidth=1
            ),
            margin=dict(l=50, r=250, t=100, b=50),
            legend=dict(
                title_text="제품명",
                font=dict(size=12)
            ),
            font=dict(
                family="Malgun Gothic, sans-serif",
                size=12
            )
        )

        st.plotly_chart(fig, use_container_width=True)


    # --- 5. 상세 데이터 테이블 표시 ---
    with st.expander("필터링된 스케줄링 상세 데이터 보기 ('우선순위', '제조번호' 포함)"):
        # 필요한 열만 선택하여 보여주기
        display_cols = ['Job', 'Task', 'BatchNo', COLS_MAP['priority'], 'Machine', 'Start_dt', 'Finish_dt', 'Duration']
        df_display = df_filtered[display_cols].copy()
        df_display['Start_dt'] = df_display['Start_dt'].dt.strftime('%Y-%m-%d %H:%M')
        df_display['Finish_dt'] = df_display['Finish_dt'].dt.strftime('%Y-%m-%d %H:%M')
        st.dataframe(df_display)


    with st.expander("원본 엑셀 데이터 보기 (정리 후)"):
        st.dataframe(df_raw)

# -----------------------------------------------------------------
# [4. 스크립트 실행]
# -----------------------------------------------------------------
if __name__ == "__main__":
    run_app()
