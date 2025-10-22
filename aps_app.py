import streamlit as st
import pandas as pd
import plotly.express as px
from ortools.sat.python import cp_model
import sys
import os
import datetime # 오늘 날짜를 가져오기 위해 import

# -----------------------------------------------------------------
# [1. (수정됨) APS 스케줄링 최적화 엔진 함수]
# (주말 제외 로직 추가)
# -----------------------------------------------------------------
def solve_job_shop_scheduling(jobs_data, all_machines, project_start_time): # [✨ project_start_time 추가 ✨]
    """
    APS 스케줄링 문제를 풀어 최적의 스케줄을 반환합니다. (주말 제외)
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
        # [✨ 주말 제외로 인해 실제 완료 시간은 horizon보다 길어질 수 있음 ✨]
        #    넉넉하게 horizon을 늘려 잡음 (예: 2배)
        #    정확하게는 주말 일수만큼 더해야 하지만, 단순화 위해 2배 적용
        #    (데이터 기간이 매우 길 경우 이 방식은 비효율적일 수 있음)
        estimated_horizon = horizon * 2 # 예상 총 기간
        if estimated_horizon == 0: estimated_horizon = 24 # 최소 기간
    except Exception as e:
        st.error(f"❌ 오류: '소요시간(H)' 열 합계 계산 중 오류: {e}")
        st.stop()

    all_priorities = [tasks[0]['priority'] for tasks in jobs_data.values()]
    if not all_priorities:
        st.error("❌ 오류: 우선순위 데이터를 찾을 수 없습니다.")
        st.stop()

    max_priority_level = max(all_priorities)
    min_priority_level = min(all_priorities)

    all_last_tasks = []

    # --- [✨ 주말 '금지 구간' 계산 ✨] ---
    weekend_definitions = [] # (start_hour, duration) 튜플 저장
    current_time = project_start_time
    end_horizon_time = project_start_time + pd.Timedelta(hours=estimated_horizon)

    # horizon 기간 내의 모든 날짜 확인
    all_dates = pd.date_range(start=project_start_time.floor('D'), end=end_horizon_time.ceil('D'), freq='D')

    for day in all_dates:
        # 토요일(5) 또는 일요일(6) 확인
        if day.dayofweek >= 5:
            # 주말 시작 시간 (00:00) 계산 (스케줄 시작 시점 기준 hour)
            weekend_start_abs = day # pd.Timestamp
            weekend_start_rel_h = int((weekend_start_abs - project_start_time).total_seconds() / 3600)

            # 주말 종료 시간 (다음 날 00:00) 계산 (스케줄 시작 시점 기준 hour)
            weekend_end_abs = day + pd.Timedelta(days=1)
            weekend_end_rel_h = int((weekend_end_abs - project_start_time).total_seconds() / 3600)

            # 주말 구간이 horizon 내에 있는지 확인 및 조정
            start_h = max(0, weekend_start_rel_h)
            end_h = min(estimated_horizon, weekend_end_rel_h)
            duration = end_h - start_h

            if duration > 0:
                weekend_definitions.append((start_h, duration))
                print(f"주말 금지 구간 추가: {day.strftime('%Y-%m-%d')} (Hour {start_h} ~ {end_h})")

    # 주말 금지 구간을 위한 Interval 변수 생성 (솔버 내부용)
    weekend_intervals = []
    for i, (w_start, w_duration) in enumerate(weekend_definitions):
         weekend_intervals.append(
             model.NewIntervalVar(w_start, w_duration, w_start + w_duration, f'weekend_{i}')
         )
    # --- [주말 계산 완료] ---


    for job_name, job_tasks in jobs_data.items():
        for i, task_info in enumerate(job_tasks):
            machine_name = task_info['machine']
            duration = int(task_info['duration'])

            task_name = f"{job_name}_공정_{i}"

            start_var = model.NewIntVar(0, estimated_horizon, f'{task_name}_start')
            end_var = model.NewIntVar(0, estimated_horizon, f'{task_name}_end')

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

            # --- [✨ 각 작업에 주말 금지 제약 추가 ✨] ---
            # 각 작업(interval_var)이 모든 주말 구간(weekend_intervals)과 겹치지 않도록 함
            # (주의: 이 방식은 작업과 주말 구간이 많으면 계산이 매우 느려질 수 있음)
            for w_interval in weekend_intervals:
                # 작업이 주말 전에 끝나거나 / 주말 후에 시작해야 함
                ends_before = model.NewBoolVar('')
                starts_after = model.NewBoolVar('')
                model.Add(interval_var.EndExpr() <= w_interval.StartExpr()).OnlyEnforceIf(ends_before)
                model.Add(interval_var.StartExpr() >= w_interval.EndExpr()).OnlyEnforceIf(starts_after)
                model.AddBoolOr([ends_before, starts_after])
            # --- [주말 제약 추가 완료] ---


        all_last_tasks.append(tasks[(job_name, len(job_tasks) - 1)]['end'])

    # 제약 조건 1: 설비 제약 (No Overlap)
    for machine_name in all_machines:
        model.AddNoOverlap(task_intervals[machine_name])

    # 제약 조건 2: 공정 순서 제약
    for job_name, job_tasks in jobs_data.items():
        for i in range(len(job_tasks) - 1):
            task_before = tasks[(job_name, i)]
            task_after = tasks[(job_name, i + 1)]
            model.Add(task_after['start'] >= task_before['end'])


    # 목표 함수: 우선순위 가중치 강화
    makespan = model.NewIntVar(0, estimated_horizon, 'makespan') # horizon -> estimated_horizon
    model.AddMaxEquality(makespan, all_last_tasks)

    objective_terms = []

    for job_name, job_tasks in jobs_data.items():
        last_task_end_var = tasks[(job_name, len(job_tasks) - 1)]['end']
        priority = job_tasks[0]['priority']
        simple_weight = (max_priority_level - priority) + 1
        weight = simple_weight ** 3
        objective_terms.append(last_task_end_var * weight)

    model.Minimize(sum(objective_terms))

    print(f"--- 우선순위 가중치 강화 (P1^3) 및 주말 제외 스케줄링 활성화 ---")

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

                bar_text = f"{job_name}<br>{task['task_name_disp']}<br>{task['batch_no']}"

                schedule_results.append(dict(
                    Job=job_name, # 오더번호
                    Machine=task['machine'],
                    Start=start_time,
                    Finish=end_time,
                    Duration=task['duration'],
                    Task=task['task_name_disp'], # 제품명
                    BatchNo=task['batch_no'], # 제조번호
                    BarText=bar_text # 조합된 텍스트 (<br> 포함)
                ))
        return schedule_results, solver.Value(makespan)
    else:
        print(f"\n❌ 스케줄을 찾지 못했습니다. (상태 코드: {status})")
        st.error(f"❌ 최적의 스케줄을 찾지 못했습니다. (솔버 상태: {status})")
        st.warning("데이터에 무리한 제약이 없는지, 또는 주말 제외로 인해 실행 가능한 스케줄이 없는지 확인하세요.")
        return None, None

# -----------------------------------------------------------------
# [2. 데이터 로드 및 처리 함수 (Streamlit 캐시 적용)]
# (수정 없음)
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

# @st.cache_data 를 run_solver 함수 정의 위에 두어야 합니다.
@st.cache_data
def run_solver(_jobs_data, _all_machines, _project_start_time): # [✨ 인자 추가 ✨]
    """
    캐시된 스케줄링 엔진을 실행합니다.
    _project_start_time 값이 바뀌면 캐시가 무효화되고 재계산됩니다.
    """
    print(f"--- APS 스케줄링 최적화 엔진 실행 (시작 시간: {_project_start_time}) ---")
    # 실제 계산에는 jobs_data와 all_machines만 필요
    return solve_job_shop_scheduling(_jobs_data, _all_machines, _project_start_time)

# -----------------------------------------------------------------
# [3. Streamlit 웹 애플리케이션 메인 로직]
# (run_solver 호출 시 project_start_time 전달)
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

    # --- 스케줄링 시작 날짜 위젯 ---
    st.sidebar.header("🗓️ 스케줄링 기준")
    scheduling_start_date = st.sidebar.date_input(
        "스케줄링 시작 날짜:",
        value=datetime.date.today()
    )
    # 선택된 날짜의 08:30:00를 스케줄링 기준 시점 (PROJECT_START_TIME)으로 설정
    PROJECT_START_TIME = pd.to_datetime(scheduling_start_date) + pd.Timedelta(hours=8, minutes=30)


    # --- 2. 데이터 로드 및 스케줄링 실행 (캐시 활용) ---
    try:
        # load_and_parse_data는 파일 경로만 필요
        jobs_data, all_machines, df_raw = load_and_parse_data(EXCEL_FILE_PATH, EXCEL_SHEET_NAME, COLS_MAP)

        if jobs_data is None:
             st.error("스케줄링할 데이터가 없습니다. (엑셀의 '소요시간(H)', '우선순위', '공정' 열 확인)")
             st.stop()

        # --- [✨ run_solver 호출 시 PROJECT_START_TIME 전달 ✨] ---
        # PROJECT_START_TIME 값이 바뀌면 run_solver 캐시가 무효화됨
        results, makespan = run_solver(jobs_data, all_machines, PROJECT_START_TIME)
        # --- [수정 완료] ---

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

    st.sidebar.info(f"총 {len(jobs_data)}개 오더\n\n총 {makespan}시간 소요\n(우선순위/주말제외 적용됨)") # 문구 수정

    # --- 4. 간트 차트 생성 및 필터링 ---

    df_results = pd.DataFrame(results) # 'BarText' 포함됨
    df_results['Start_dt'] = PROJECT_START_TIME + pd.to_timedelta(df_results['Start'], unit='h')
    df_results['Finish_dt'] = PROJECT_START_TIME + pd.to_timedelta(df_results['Finish'], unit='h')

    # 차트 X축 범위
    start_datetime_chart = pd.to_datetime(start_date)
    start_datetime_view = start_datetime_chart.floor('D')
    end_datetime_view = start_datetime_view + pd.to_timedelta(view_days, unit='d')


    # (필터링 로직)
    merge_cols = [COLS_MAP['id'], COLS_MAP['department'], COLS_MAP['display'], COLS_MAP['priority'], COLS_MAP['batch']]
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
            text="BarText",  # 막대 텍스트: 조합된 텍스트 (<br> 포함)
            title=f"APS 스케줄링 결과 (총 {makespan}시간)",
            hover_data=[COLS_MAP['priority'], COLS_MAP['batch']]
        )

        # 주석 방식은 <br>을 인식하므로 텍스트 위치만 조정
        fig.update_traces(textposition='middle center', textfont_size=10) # 중앙 정렬 및 폰트 크기 조절

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
        display_cols = ['Job', 'Task', 'BatchNo', COLS_MAP['priority'], 'Machine', 'Start_dt', 'Finish_dt', 'Duration']
        df_display = df_filtered[display_cols].copy()
        # [✨ 시간 표시 형식 수정 ✨] - 날짜만 필요하면 %H:%M 제거
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
