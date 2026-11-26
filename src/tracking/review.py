"""A step-by-step notebook UI for road-plane calibration and review."""

import json
import math
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from .calibration import (
    adjust_krt,
    confirm_camera_group,
    create_calibration_previews,
    export_krt,
    fit_homography,
    fit_krt,
    frame_number,
    read_registry,
    reference_images,
    save_calibration,
    save_group_calibration,
)
from .common import fingerprint, sha256_file, write_json


def calibration_widget(
    dataset, registry, sequence=None, output=None, detections=None
):
    """Collect corners, preview geometry, then explicitly approve a calibration."""
    import ipywidgets as w
    from ipyevents import Event
    from IPython.display import clear_output, display

    dataset, registry = Path(dataset), Path(registry)
    output = Path(output or registry.parent / "previews")
    inventory = read_registry(registry)["sequences"]
    names = sorted(
        inventory, key=lambda s: (inventory[s]["split"] != "val", s)
    )
    style = {"description_width": "125px"}
    select = w.Dropdown(
        options=names,
        value=sequence or names[0],
        description="Video:",
        style=style,
    )
    frames = w.Dropdown(description="Ảnh tham chiếu:", style=style)
    points = w.Textarea(
        value="[]",
        description="Tọa độ JSON:",
        layout=w.Layout(width="100%"),
        style=style,
    )
    width = w.FloatText(value=3.5, description="Rộng 1→2 (m):", style=style)
    length = w.FloatText(value=0.0, description="Dài 2→3 (m):", style=style)
    evidence = w.Textarea(
        description="Nguồn kích thước:",
        style=style,
        placeholder="Ví dụ: rộng 3,5 m là giả định; dài 12 m chưa được đo.",
        layout=w.Layout(width="100%"),
    )
    reviewer = w.Text(description="Người duyệt:", style=style)
    notes = w.Textarea(
        description="Ghi chú:", style=style, layout=w.Layout(width="100%")
    )
    method = w.Dropdown(
        options=[
            ("Homography (4 điểm)", "homography"),
            ("KRT (camera nâng cao)", "krt"),
        ],
        description="Phương pháp:",
        style=style,
    )
    focal = w.FloatSlider(
        value=1000.0,
        min=100,
        max=10000,
        step=10,
        description="Focal (pixel):",
        style=style,
        continuous_update=False,
    )
    use_vp = w.Checkbox(description="Ước lượng focal từ hai hướng vuông góc")
    lines = w.Textarea(
        value="[]",
        description="Đường VP JSON:",
        style=style,
        layout=w.Layout(width="100%"),
    )
    rotation = [
        w.FloatSlider(
            description=f"Xoay {axis} (độ):",
            min=-20,
            max=20,
            step=0.1,
            continuous_update=False,
            style=style,
        )
        for axis in "XYZ"
    ]
    shift = [
        w.FloatSlider(
            description=f"Dịch {axis} (m):",
            min=-20,
            max=20,
            step=0.1,
            continuous_update=False,
            style=style,
        )
        for axis in "XYZ"
    ]
    confirm = w.Checkbox(
        description="Tôi đã kiểm tra điểm, kích thước và cả 3 ảnh preview.",
        disabled=True,
        indent=False,
        layout=w.Layout(width="100%"),
    )
    group_name = w.Text(description="Nhóm camera:", style=style)
    group_confirm = w.Checkbox(
        description="Tôi đã kiểm tra cùng góc nhìn, crop, zoom và độ phân giải."
    )
    show_grid = w.Checkbox(description="Hiện lưới tọa độ pixel", value=False)
    source_view = w.Image(
        format="png", layout=w.Layout(max_width="100%", height="auto")
    )
    corner_hint, preview_status = w.HTML(), w.HTML()
    preview_output, messages = w.Output(), w.Output()
    state = dict(loading=False, previewed=False)

    def corner_list():
        corners = json.loads(points.value)
        if not isinstance(corners, list) or len(corners) > 4:
            raise ValueError("Cần một danh sách gồm tối đa 4 điểm.")
        for point in corners:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or not all(
                    isinstance(v, (int, float)) and math.isfinite(v)
                    for v in point
                )
            ):
                raise ValueError("Mỗi điểm phải có hai tọa độ hữu hạn [x, y].")
        return corners

    def update_ready(*_):
        try:
            count = len(corner_list())
            valid = count == 4 and all(
                math.isfinite(v) and v > 0 for v in (width.value, length.value)
            )
        except (ValueError, TypeError):
            count, valid = 0, False
        undo.disabled = count == 0
        clear.disabled = points.value.strip() == "[]"
        fit.disabled = draft.disabled = not valid
        confirm.disabled = not state["previewed"]
        approved.disabled = not (
            valid
            and state["previewed"]
            and confirm.value
            and reviewer.value.strip()
            and evidence.value.strip()
        )
        preview_status.value = (
            "<b>Đã tạo preview.</b> Kiểm tra cả 3 ảnh rồi xác nhận ở bước 5."
            if state["previewed"]
            else "Chưa có preview cho lựa chọn hiện tại. Chọn đủ 4 điểm và nhập cả hai kích thước."
        )

    def invalidate(*_):
        if state["loading"]:
            return
        state["previewed"] = False
        confirm.value = group_confirm.value = False
        with preview_output:
            clear_output(wait=False)
        update_ready()

    def change_review_details(*_):
        # Names and notes do not change geometry, but require a fresh confirmation.
        if not state["loading"]:
            confirm.value = group_confirm.value = False
            update_ready()

    def show_source(*_):
        if frames.value is None:
            return
        with Image.open(frames.value) as opened:
            image = opened.convert("RGB")
        draw = ImageDraw.Draw(image)
        if show_grid.value:
            for x in range(0, image.width, 100):
                draw.line((x, 0, x, image.height), fill="gray")
                draw.text((x + 2, 2), str(x), fill="yellow")
            for y in range(0, image.height, 100):
                draw.line((0, y, image.width, y), fill="gray")
                draw.text((2, y + 2), str(y), fill="yellow")
        try:
            corners = corner_list()
            if len(corners) > 1:
                boundary = (
                    corners + corners[:1] if len(corners) == 4 else corners
                )
                draw.line([tuple(p) for p in boundary], fill="red", width=3)
                draw.line(
                    [tuple(p) for p in corners[:2]], fill="dodgerblue", width=5
                )
            if len(corners) > 2:
                draw.line(
                    [tuple(p) for p in corners[1:3]], fill="orange", width=5
                )
            for number, (x, y) in enumerate(corners, 1):
                draw.ellipse(
                    (x - 12, y - 12, x + 12, y + 12),
                    fill="white",
                    outline="red",
                    width=3,
                )
                draw.text((x - 3, y - 6), str(number), fill="black")
            prompts = [
                "Bấm góc 1: một đầu của cạnh ngang làn đường.",
                "Bấm góc 2: đầu còn lại của cạnh rộng 1→2.",
                "Bấm góc 3: đi dọc làn đường từ góc 2.",
                "Bấm góc 4: góc còn lại để khép vùng mặt đường.",
                "Đã đủ 4 góc. Kiểm tra cạnh xanh 1→2 và cạnh cam 2→3.",
            ]
            corner_hint.value = (
                f"<b>{len(corners)}/4 điểm.</b> {prompts[len(corners)]}"
            )
        except (ValueError, TypeError):
            corner_hint.value = (
                "Tọa độ chưa hợp lệ. Sửa JSON hoặc bấm Chọn lại từ đầu."
            )
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        source_view.value = buffer.getvalue()

    def pick_corner(event):
        if event.get("type") != "click" or event.get("button", 0) != 0:
            return
        with messages:
            clear_output(wait=True)
            try:
                corners = corner_list()
                if len(corners) == 4:
                    print(
                        "Đã đủ 4 điểm. Bấm Bỏ điểm cuối hoặc Chọn lại từ đầu để sửa."
                    )
                    return
                # Browser data coordinates already account for display scaling.
                x, y = float(event["dataX"]), float(event["dataY"])
                with Image.open(frames.value) as image:
                    if not (
                        math.isfinite(x)
                        and math.isfinite(y)
                        and 0 <= x < image.width
                        and 0 <= y < image.height
                    ):
                        return
                points.value = json.dumps(
                    corners + [[round(x, 2), round(y, 2)]]
                )
            except (ValueError, TypeError, KeyError) as exc:
                print(f"Chưa chọn được điểm: {exc}")

    source_view._corner_events = Event(
        source=source_view, watched_events=["click"]
    )
    source_view._corner_events.on_dom_event(pick_corner)

    def undo_corner():
        points.value = json.dumps(corner_list()[:-1])

    def change_frame(*_):
        if not state["loading"]:
            points.value = lines.value = "[]"
            show_source()

    def change_sequence(*_):
        state["loading"] = True
        points.value, lines.value = "[]", "[]"
        evidence.value, notes.value = "", ""
        length.value = 0.0
        for control in rotation + shift:
            control.value = 0
        refs = reference_images(dataset, select.value)
        labels = ["Đầu", "Giữa", "Cuối"]
        frames.options = [
            (f"{labels[i]} — frame {frame_number(p)}", str(p))
            for i, p in enumerate(refs)
        ]
        frames.value = str(refs[len(refs) // 2])
        group_name.value = (
            read_registry(registry)["sequences"][select.value].get(
                "camera_group"
            )
            or ""
        )
        state["loading"] = False
        invalidate()
        show_source()

    def load_saved():
        """Restore editable inputs; a saved review never checks the approval box."""
        record = read_registry(registry)["sequences"][select.value][
            "methods"
        ].get(method.value, {})
        if "image_points" not in record:
            raise ValueError(
                "Video này chưa có bản lưu cho phương pháp đang chọn."
            )
        state["loading"] = True
        try:
            for _, path in frames.options:
                if frame_number(path) == record.get("source_frame"):
                    frames.value = path
                    break
            width.value = record["dimensions_m"]["width"]
            length.value = record["dimensions_m"]["length"]
            evidence.value = record.get("evidence", "")
            notes.value = record.get("limitations", "")
            for control in rotation + shift:
                control.value = 0
            focal.value = record.get("focal_px", 1000.0)
            lines.value = json.dumps(record.get("vanishing_lines", []))
            use_vp.value = (
                record.get("focal_source") == "orthogonal_vanishing_points"
            )
            points.value = json.dumps(record["image_points"])
        finally:
            state["loading"] = False
        invalidate()
        show_source()
        print("Đã nạp bản lưu. Kiểm tra kích thước ở bước 3 rồi bấm Xem thử.")

    def compute():
        with Image.open(frames.value) as opened:
            size = list(opened.size)
        corners = corner_list()
        if method.value == "homography":
            record = fit_homography(corners, width.value, length.value, size)
        else:
            record = fit_krt(
                corners,
                width.value,
                length.value,
                size,
                vanishing_lines=(
                    json.loads(lines.value) if use_vp.value else None
                ),
                focal=None if use_vp.value else focal.value,
            )
            if any(c.value for c in rotation + shift):
                record = adjust_krt(
                    record,
                    [c.value for c in rotation],
                    [c.value for c in shift],
                )
        record.update(
            source_frame=frame_number(frames.value),
            source_image_sha256=sha256_file(frames.value),
            evidence=evidence.value.strip(),
        )
        return record

    def preview_current():
        record = compute()
        csv = Path(detections) / f"{select.value}.csv" if detections else None
        paths = create_calibration_previews(
            dataset,
            select.value,
            record,
            output / select.value / method.value,
            csv,
        )
        with preview_output:
            clear_output(wait=True)
            for path in paths:
                with Image.open(path) as opened:
                    display(opened.copy())
        state["previewed"] = True
        update_ready()
        return record

    def save(reviewed):
        if reviewed and not (state["previewed"] and confirm.value):
            raise ValueError(
                "Cần xem preview và xác nhận kiểm tra trước khi duyệt."
            )
        record = preview_current()
        record = save_calibration(
            registry,
            select.value,
            record,
            reviewed,
            reviewer.value,
            notes.value,
        )
        provenance = output / select.value / method.value / "provenance.json"
        saved = json.loads(provenance.read_text(encoding="utf-8"))
        saved.update(
            calibration=record, calibration_sha256=fingerprint(record)
        )
        write_json(provenance, saved)
        if method.value == "krt":
            export_krt(
                record, registry.parent / "cam_para" / f"{select.value}.txt"
            )
        confirm.value = False
        print(
            "Đã lưu bản đã duyệt."
            if reviewed
            else "Đã lưu bản nháp, chưa duyệt."
        )

    def share():
        save_group_calibration(
            registry, select.value, method.value, group_name.value
        )
        print(
            "Đã lưu camera tham chiếu cho nhóm. Các video khác vẫn cần xác nhận."
        )

    def join():
        if not group_confirm.value:
            raise ValueError("Cần xác nhận các video có cùng hình học camera.")
        confirm_camera_group(
            registry,
            select.value,
            group_name.value,
            reviewer.value,
            notes.value,
        )
        group_confirm.value = False
        print("Đã xác nhận video thuộc nhóm camera.")

    def button(label, action, color=""):
        control = w.Button(
            description=label,
            button_style=color,
            layout=w.Layout(width="auto", min_width="160px"),
        )

        def click(_):
            with messages:
                clear_output(wait=True)
                try:
                    action()
                except Exception as exc:
                    print(f"Chưa thực hiện được: {exc}")

        control.on_click(click)
        return control

    undo = button("Bỏ điểm cuối", undo_corner)
    clear = button("Chọn lại từ đầu", lambda: setattr(points, "value", "[]"))
    fit = button("Xem thử phép chiếu", preview_current, "info")
    draft = button("Lưu bản nháp", lambda: save(False))
    approved = button(
        "Lưu và xác nhận đã duyệt", lambda: save(True), "success"
    )
    krt = w.VBox(
        [
            w.HTML(
                "Chỉ dùng khi cần mô hình K/R/T. VP JSON có cấu trúc "
                "[2 hướng][2 đoạn][2 đầu mút][x,y]."
            ),
            use_vp,
            focal,
            lines,
            *rotation,
            *shift,
        ]
    )
    advanced = w.Accordion(
        children=[w.VBox([method, krt]), w.VBox([show_grid, points])],
        selected_index=None,
    )
    advanced.set_title(0, "Nâng cao: mô hình camera KRT")
    advanced.set_title(1, "Nâng cao: tọa độ JSON và lưới pixel")
    groups = w.Accordion(
        children=[
            w.VBox(
                [
                    w.HTML(
                        "Chỉ chia sẻ khi cùng góc nhìn, crop, zoom và độ phân giải. "
                        "Bản riêng của video được ưu tiên hơn bản của nhóm."
                    ),
                    group_name,
                    group_confirm,
                    button("Lưu camera cho nhóm", share),
                    button("Xác nhận video vào nhóm", join),
                ]
            )
        ],
        selected_index=None,
    )
    groups.set_title(0, "Nâng cao: chia sẻ camera giữa các video")

    def change_method(*_):
        krt.layout.display = "" if method.value == "krt" else "none"

    change_method()
    method.observe(change_method, names="value")
    for control in [
        points,
        frames,
        width,
        length,
        method,
        focal,
        use_vp,
        lines,
        *rotation,
        *shift,
    ]:
        control.observe(invalidate, names="value")
    for control in [evidence, reviewer, notes, group_name]:
        control.observe(change_review_details, names="value")
    confirm.observe(update_ready, names="value")
    points.observe(show_source, names="value")
    show_grid.observe(show_source, names="value")
    frames.observe(change_frame, names="value")
    select.observe(change_sequence, names="value")
    change_sequence()

    diagram = w.HTML(
        "<p>Chọn một vùng hình chữ nhật trên mặt đường phẳng. Trên ảnh, vùng này "
        "thường có dạng hình thang. Bấm theo đường bao <b>1 → 2 → 3 → 4</b>.</p>"
        '<svg viewBox="0 0 340 150" width="340" height="150" role="img" '
        'aria-label="Thứ tự bốn góc và hai kích thước">'
        '<polygon points="70,120 230,120 200,25 100,25" fill="#f5f5f5" stroke="#b33" stroke-width="2"/>'
        '<path d="M70 120H230" stroke="#1682cf" stroke-width="4"/>'
        '<path d="M230 120L200 25" stroke="#e68b00" stroke-width="4"/>'
        '<g font-size="16"><text x="52" y="130">1</text><text x="235" y="130">2</text>'
        '<text x="205" y="23">3</text><text x="82" y="23">4</text>'
        '<text x="115" y="145" fill="#1682cf">Chiều rộng</text>'
        '<text x="240" y="70" fill="#a65c00">Chiều dài</text></g></svg>'
        "<p>Ảnh thực có thể xoay khác sơ đồ. Cạnh <b>xanh 1→2</b> là chiều rộng; "
        "cạnh <b>cam 2→3</b> là chiều dài. Mỗi điểm phải nằm trên mặt đường, "
        "không đặt trên xe, lan can hoặc cột.</p>"
    )
    return w.VBox(
        [
            w.HTML(
                "<h2>Chọn 4 góc mặt đường</h2>Thực hiện lần lượt 5 bước bên dưới. "
                "Các thông số nâng cao được thu gọn."
            ),
            w.HTML(
                "<h3>1. Chọn video và ảnh</h3>Đổi ảnh/video sẽ xóa các điểm đang chọn. "
                "Có thể nạp bản đã chọn sẵn rồi chỉnh lại."
            ),
            select,
            frames,
            button("Nạp bản đã lưu", load_saved),
            w.HTML("<h3>2. Bấm 4 góc trên ảnh</h3>"),
            diagram,
            corner_hint,
            source_view,
            w.HBox([undo, clear], layout=w.Layout(flex_flow="row wrap")),
            w.HTML(
                "<h3>3. Nhập kích thước thật của vùng đường</h3>"
                "Đơn vị mét. <b>3,5 m chỉ là gợi ý chiều rộng.</b> Chiều dài phải "
                "nhập riêng. Nếu chưa đo, ghi rõ là giả định và lưu bản nháp."
            ),
            width,
            length,
            evidence,
            w.HTML(
                "<h3>4. Xem thử</h3>Kiểm tra lưới chiếu, ảnh nhìn từ trên xuống và "
                "các chấm đáy bbox trên cả ảnh đầu, giữa và cuối. "
                "Preview chưa chứng minh kích thước giả định là chính xác."
            ),
            fit,
            preview_status,
            preview_output,
            w.HTML(
                "<h3>5. Lưu kết quả</h3>Lưu nháp nếu chưa chắc chắn. "
                "Chỉ duyệt sau khi đã xem preview và kiểm tra nguồn kích thước."
            ),
            reviewer,
            notes,
            confirm,
            w.HBox([draft, approved], layout=w.Layout(flex_flow="row wrap")),
            advanced,
            groups,
            messages,
        ]
    )
