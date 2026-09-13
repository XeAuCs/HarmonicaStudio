"""Thin desktop presentation adapter; workflow decisions belong to the controller."""


class DesktopPresenter:
    def __init__(self, view, controller):
        self.view, self.controller = view, controller
        self.unsubscribe = controller.subscribe(self.on_event)

    def invoke(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            self.controller.report_error(exc)

    def on_event(self, event, value):
        view = self.view
        if event == 'document':
            view.render_document()
        elif event == 'parts':
            view.install_parts()
        elif event == 'library':
            view.watch_library()
            view.render_library()
        elif event == 'library_requested':
            view.watch_library()
        elif event == 'close_ready':
            view.finish_close()
        elif event == 'changed':
            view.update_summary()
            view.refresh_controls()
        elif event == 'position':
            state = self.controller.state
            view.display_position(state.position, state.position_label, state.show_cursor)
        elif event == 'reset_timeline':
            view.roll.reset_timeline()
        elif event == 'result':
            view.tabs.setCurrentIndex(1)
            view.filename.setText(self.controller.state.project['title'])
        elif event == 'autosave':
            view.autosave_timer.start(400)
        elif event == 'status':
            view.status.setText(value)
        elif event == 'error':
            exc, remote = value
            if not remote:
                view.show_error(exc)

    def close(self):
        self.unsubscribe()
