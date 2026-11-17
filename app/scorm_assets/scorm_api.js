/* Минимальная обёртка SCORM 1.2 API.
   Ищет объект API в цепочке родительских/открывающих окон (стандартный паттерн LMS),
   инициализирует сессию и даёт простые методы set/commit/finish. */
(function (global) {
  "use strict";

  function findAPI(win) {
    var attempts = 0;
    while (win && !win.API && win.parent && win.parent !== win && attempts < 10) {
      attempts++;
      win = win.parent;
    }
    return win ? win.API : null;
  }

  function locateAPI() {
    var api = findAPI(window);
    if (!api && window.opener) {
      api = findAPI(window.opener);
    }
    return api;
  }

  var ScormAPI = {
    api: null,
    initialized: false,

    init: function () {
      this.api = locateAPI();
      if (!this.api) {
        console.warn("[SCORM] LMS API не найден — запуск вне LMS (локальный просмотр).");
        return false;
      }
      var result = this.api.LMSInitialize("");
      this.initialized = result === "true" || result === true;
      if (this.initialized) {
        this.api.LMSSetValue("cmi.core.lesson_status", "incomplete");
        this.api.LMSCommit("");
      }
      return this.initialized;
    },

    setValue: function (key, value) {
      if (!this.initialized) return;
      this.api.LMSSetValue(key, value);
      this.api.LMSCommit("");
    },

    setProgress: function (ratio /* 0..1 */) {
      // SCORM 1.2 не имеет стандартного прогресса в процентах — используем suspend_data
      this.setValue("cmi.suspend_data", JSON.stringify({ progress: ratio }));
    },

    complete: function (passed) {
      if (!this.initialized) return;
      this.setValue("cmi.core.lesson_status", passed === false ? "failed" : "completed");
      this.api.LMSCommit("");
    },

    finish: function () {
      if (!this.initialized) return;
      this.api.LMSFinish("");
    },
  };

  global.ScormAPI = ScormAPI;
})(window);
