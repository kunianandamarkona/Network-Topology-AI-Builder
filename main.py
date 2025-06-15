import streamlit as st
import subprocess
import re
import os
import signal
import time
from llm_agent import clean_version_in_input, get_yaml, destroy_lab

GRAPH_URL = "http://10.77.92.106:50080/"
yaml_file = os.path.abspath("current_lab.yml")

def parse_clab_deploy_output(deploy_output):
    results = []
    pattern = r"clab-[\w\-]+-([\w\d]+)\s+\│[^\│]+\│[^\│]+\│\s+([\d\.]+)\s+\│"
    matches = re.findall(pattern, deploy_output)
    for node, ip in matches:
        results.append({'node': node, 'ip': ip})
    return results

def show_router_access_table(node_list):
    st.subheader("🔑 Router SSH Access")
    st.write("""
    **How to log in:**<br>
    1. Open a terminal on this server (or your jump host)<br>
    2. Run the SSH command below for your router<br>
    **Username:** `clab`  **Password:** `clab@123`
    """, unsafe_allow_html=True)
    for node in node_list:
        ssh_cmd = f"ssh clab@{node['ip']}"
        cols = st.columns([2, 2, 3, 1])
        cols[0].write(f"**{node['node']}**")
        cols[1].write(node['ip'])
        cols[2].code(ssh_cmd, language='bash')
        copy_id = f"copy_{node['node']}"
    st.info("Copy the SSH command and paste it in your own terminal on the server or via your jump host.")

def restart_containerlab_graph(topology_file, port=50080):
    try:
        ps = subprocess.Popen(['ps', 'aux'], stdout=subprocess.PIPE)
        grep = subprocess.Popen(['grep', 'containerlab graph'], stdin=ps.stdout, stdout=subprocess.PIPE)
        awk = subprocess.Popen(['awk', '{print $2}'], stdin=grep.stdout, stdout=subprocess.PIPE)
        ps.stdout.close()
        grep.stdout.close()
        pids = awk.communicate()[0].decode('utf-8').split()
        for pid in pids:
            if pid and pid != str(os.getpid()):
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except Exception:
                    pass
    except Exception as e:
        print(f"Error killing old graph processes: {e}")

    try:
        subprocess.Popen(
            ["containerlab", "graph", "-t", topology_file],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return True
    except Exception as e:
        print(f"Failed to start containerlab graph: {e}")
        return False

def activate_sshx_and_get_link(cmd, max_wait=120):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sshx_link = None
    output_lines = []
    start_time = time.time()
    while True:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            time.sleep(0.1)
            continue
        output_lines.append(line)
        # Show each line for debugging
        st.write(f"LINE: {line.strip()}")
        # Extract link
        if "link=https://sshx.io/s/" in line:
            sshx_link = line.split("link=")[-1].strip()
            break
        m = re.search(r'(https://sshx\.io/s/[A-Za-z0-9#]+)', line)
        if m:
            sshx_link = m.group(1)
            break
        if time.time() - start_time > max_wait:
            break
    # Capture remaining output
    remaining = proc.communicate()[0]
    if remaining:
        output_lines.append(remaining)
    full_output = "".join(output_lines)
    return sshx_link, full_output

st.set_page_config(page_title="Bangalore CX Topology Builder", layout="wide")
st.title("Bangalore CX AI Topology Builder")
# Place Deep Clean button in top-right
top_left, top_right = st.columns([7, 1])
with top_right:
    if st.button("🧹 Deep Clean"):
        # Step 1: Destroy lab
        subprocess.run(
            ["containerlab", "destroy", "-t", yaml_file, "--cleanup"],
            capture_output=True, text=True
        )
        # Step 2: Remove any leftover containers
        subprocess.run(
            "docker ps -a --format '{{.Names}}' | grep clab-Cisco_Internal | xargs -r docker rm -f",
            shell=True, capture_output=True, text=True
        )
        # Step 3: Remove management network
        subprocess.run(
            ["docker", "network", "rm", "clab_cisco_internal"],
            capture_output=True, text=True
        )
        # Only show this message:
        st.success("Deep clean complete!")
if "yaml" not in st.session_state:
    st.session_state.yaml = ""

if "node_list" not in st.session_state:
    st.session_state.node_list = []

col1, col2 = st.columns([2, 3])

with col1:
    st.header("📝 Topology Input")
    user_input = st.text_area("Describe your topology (e.g. R1--R2--R3, V7.11)", value="", height=80)
    gen_yaml = st.button("Generate YAML")
    deploy = st.button("Deploy Topology")
    destroy = st.button("Destroy Lab")

    if gen_yaml and user_input.strip():
        user_input_cleaned = clean_version_in_input(user_input)
        messages = [{"role": "user", "content": user_input_cleaned}]
        try:
            ai_reply = get_yaml(messages)
            st.session_state.yaml = ai_reply
            with open(yaml_file, "w") as f:
                f.write(ai_reply)
            st.success(f"YAML generated and saved to {yaml_file}.")
        except Exception as e:
            st.error(f"Error: {e}")

    if st.session_state.yaml:
        st.subheader("📄 Generated YAML")
        st.code(st.session_state.yaml, language="yaml")

    if deploy:
        try:
            with st.spinner("Destroying any existing lab..."):
                destroy_lab(yaml_file)
            with st.spinner("Deploying new topology..."):
                result = subprocess.run(
                    ["containerlab", "deploy", "-t", yaml_file],
                    capture_output=True, text=True, check=True
                )
                deploy_output = result.stdout
            st.success("Lab deployed.")
            st.text_area("Containerlab Deploy Output", deploy_output, height=200)
            node_list = parse_clab_deploy_output(deploy_output)
            if node_list:
                st.session_state.node_list = node_list
            else:
                st.warning("No routers found in deploy output!")
        except Exception as e:
            st.error(f"Deploy failed: {e}")

    if destroy:
        destroy_lab(yaml_file)
        st.success("Lab destroyed.")
        st.session_state.node_list = []

with col2:
    node_list = st.session_state.get('node_list', [])
    if node_list:
        show_router_access_table(node_list)
    else:
        st.info("No routers found. Deploy a topology to see access options.")

    st.markdown("---")
    st.subheader("🖥️ Router Console Access")

    # Button: Clean up old SSHX session
    if st.button("Clean Up Old SSHX Session"):
        result = subprocess.run(
            ["docker", "rm", "-f", "clab-Cisco_Internal-sshx"],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            st.success("Old SSHX session (if any) cleaned up successfully.")
        else:
            st.info("No old SSHX session found or error during cleanup.")
        if result.stdout.strip():
            st.code(result.stdout.strip())
        if result.stderr.strip():
            st.code(result.stderr.strip())

    # Button: Activate SSHX session and show link (real-time output)
    if st.button("Activate Console Access"):
        with st.spinner("Starting SSHX session... (this may take up to 30 seconds)"):
            cmd = [
                "containerlab", "tools", "sshx", "attach",
                "-t", yaml_file
            ]
            sshx_link, sshx_output = activate_sshx_and_get_link(cmd, max_wait=120)
            st.markdown("**SSHX Output:**")
            st.code(sshx_output)
            if sshx_link:
                st.success(f"SSHX Console link: {sshx_link}")
                st.markdown(f'[Open Terminal Session in new tab]({sshx_link})', unsafe_allow_html=True)
                st.markdown(f'<iframe src="{sshx_link}" width="100%" height="600px"></iframe>', unsafe_allow_html=True)
            else:
                st.error("Could not find SSHX link in output.")

    st.markdown("---")
    topo_viz = st.button("Topology Visualization")
    if topo_viz:
        success = restart_containerlab_graph(yaml_file, port=50080)
        if success:
            st.success("Started/restarted containerlab graph server.")
        else:
            st.error("Failed to start containerlab graph server.")

    st.subheader("🖥️ Topology Graph Viewer")
    st.markdown(f"""
    <iframe src="{GRAPH_URL}" width="100%" height="600px" style="border:1px solid #ccc"></iframe>
    """, unsafe_allow_html=True)
    st.info(f"Topology graph will appear here if the server is running at {GRAPH_URL}")
